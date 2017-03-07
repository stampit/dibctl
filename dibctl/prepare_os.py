import osclient
import timeout
import sys
import uuid
import socket
import time
import os
import json
import tempfile
import config


class TimeoutError(EnvironmentError):
    pass


class InstanceError(EnvironmentError):
    pass


class NewImage(object):
    pass


class ExistingImage(object):
    pass


class NewInstance(object):
    pass


class ExistingInstance(object):
    pass


class PrepOS(object):

    'Provides test-specific image/instance/keypair with timeouts and cleanup at errors'
    LONG_OS_TIMEOUT = 360
    SHORT_OS_TIMEOUT = 10
    SLEEP_DELAY = 3

    def __init__(self, image, test_environment, override_image=None, delete_image=True, delete_instance=True):
        self.os = None
        self.set_timeouts(image, test_environment)
        self.test_environment = test_environment
        self.report = True  # refactor me!
        if override_image:
            self.prepare_override_image(image, override_image)
        else:
            self.prepare_normal_image(image, delete_image)

        self.prepare_key()
        self.prepare_instance(test_environment, delete_instance)

    def set_timeouts(self, image_item, tenv_item):
        self.upload_timeout = config.get_max(
            image_item,
            tenv_item,
            'glance.upload_timeout',
            self.LONG_OS_TIMEOUT
        )
        self.keypair_timeout = config.get_max(
            image_item,
            tenv_item,
            'nova.keypair_timeout',
            self.SHORT_OS_TIMEOUT
        )
        self.cleanup_timeout = config.get_max(
            image_item,
            tenv_item,
            'nova.cleanup_timeout',
            self.SHORT_OS_TIMEOUT
        )
        self.active_timeout = config.get_max(
            image_item,
            tenv_item,
            'nova.active_timeout',
            self.LONG_OS_TIMEOUT
        )
        self.create_timeout = config.get_max(
            image_item,
            tenv_item,
            'nova.create_timeout',
            self.LONG_OS_TIMEOUT
        )

    def prepare_normal_image(self, image_item, delete_image_flag):
        self.image = image_item
        self.image_name = self.make_test_name(image_item['glance']['name'])
        self.delete_image = delete_image_flag
        self.os_image = None
        self.override_image = False

    def prepare_override_image(self, image_item, override_image_uuid):
        self.image = image_item
        self.os_image = self.get_image(override_image_uuid)
        self.image_name = self.os_image.name
        print("Found image %s (%s)" % (self.os_image.id, self.os_image.name))
        self.delete_image = False
        self.override_image = True  # refactor me

    def get_image(self, image):
        self.connect()
        return self.os.get_image(image)

    def prepare_key(self):
        self.key_name = self.make_test_name('key')
        self.os_key = None
        self.delete_keypair = True

    def prepare_instance(self, tenv_item, delete_instance_flag):
        self.instance_name = self.make_test_name('test')
        self.os_instance = None
        self.config_drive = tenv_item['nova'].get('config_drive', False)
        self.availability_zone = tenv_item['nova'].get('availability_zone', None)
        self.delete_instance = delete_instance_flag
        self.flavor_id = tenv_item['nova']['flavor']
        self.nic_list = list(self.prepare_nics(tenv_item['nova']))
        self.main_nic_regexp = tenv_item['nova'].get('main_nic_regexp', None)

    def connect(self):
        if not self.os:
            print("Connecting to Openstack")
            self.os = osclient.OSClient(
                keystone_data=self.test_environment['keystone'],
                nova_data=self.test_environment['nova'],
                glance_data=self.image.get('glance'),
                neutron_data=self.test_environment.get('neutron'),
                overrides=os.environ,
                ca_path=self.test_environment.get('ssl_ca_path', '/etc/ssl/certs'),
                insecure=self.test_environment.get('ssl_insecure', False)
            )

    @staticmethod
    def prepare_nics(env):
        for nic in env.get('nics', []):
            response = {}
            if 'net_id' in nic:
                response['net-id'] = nic['net_id']
            # TODO add fixed IP/mac/etc
            yield response

    @staticmethod
    def make_test_name(bare_name):
        return 'DIBCTL-%s-%s' % (bare_name, str(uuid.uuid4()))

    def init_keypair(self):
        with timeout.timeout(self.keypair_timeout, self.error_handler):
            self.os_key = self.os.new_keypair(self.key_name)

    def save_private_key(self):
        f = tempfile.NamedTemporaryFile(prefix='DIBCTL_ssh_key', suffix='private', delete=False)
        f.write(self.os_key.private_key)
        f.close()
        self.os_key_private_file = f.name

    def wipe_private_key(self):
        with open(self.os_key_private_file, 'w') as f:
            f.write(' ' * 4096)
        os.remove(self.os_key_private_file)

    def upload_image(self, timeout_s):
        with timeout.timeout(timeout_s, self.error_handler):
            if not self.override_image:
                filename = self.image['filename']
                print("Uploading image from %s (time limit is %s s)" % (filename, timeout_s))
                self.os_image = self.os.upload_image(
                    self.image_name,
                    filename,
                    meta=self.image['glance'].get('properties', {})
                )
                print("Image %s uploaded." % self.os_image)

    def spawn_instance(self, timeout_s):
        print("Creating test instance (time limit is %s s)" % timeout_s)
        with timeout.timeout(timeout_s, self.error_handler):
            self.os_instance = self.os.boot_instance(
                name=self.instance_name,
                image_uuid=self.os_image,
                flavor=self.flavor_id,
                key_name=self.os_key.name,
                nic_list=self.nic_list,
                config_drive=self.config_drive,
                availability_zone=self.availability_zone
            )
            print("Instance %s created." % self.os_instance.id)

    def get_instance_main_ip(self):
        self.ip = self.os.get_instance_ip(self.os_instance, self.main_nic_regexp)
        return self.ip

    def wait_for_instance(self, timeout_s):
        print("Waiting for instance to become active (time limit is %s s)" % timeout_s)
        with timeout.timeout(timeout_s, self.error_handler):
            while self.os_instance.status != 'ACTIVE':
                if self.os_instance.status in ('ERROR', 'DELETED'):
                    raise InstanceError(
                        "Instance %s state is '%s' (expected 'ACTIVE')." % (
                            self.os_instance.id,
                            self.os_instance.status
                        )
                    )
                time.sleep(self.SLEEP_DELAY)
                self.os_instance = self.os.get_instance(self.os_instance.id)
        print("Instance become active.")

    def prepare(self):
        self.init_keypair()
        #  self.save_private_key()  #  remove, refactoring
        sys.stdout.flush()
        self.upload_image(self.upload_timeout)
        sys.stdout.flush()
        self.spawn_instance(self.create_timeout)
        sys.stdout.flush()
        self.wait_for_instance(self.active_timeout)
        sys.stdout.flush()
        self.get_instance_main_ip()
        sys.stdout.flush()

    @staticmethod
    def _cleanup(name, obj, flag, call):
        try:
            if obj:
                if flag:
                    print("Removing %s." % name)
                    call(obj)
                else:
                    print("Not removing %s." % name)
        except Exception as e:
            print("Error while clear up %s: %s" % (name, e))

    def cleanup_instance(self):
        self._cleanup(
            'instance',
            self.os_instance,
            self.delete_instance,
            self.os.delete_instance
        )

    def cleanup_image(self):
        self._cleanup(
            'image',
            self.os_image,
            self.delete_image,
            self.os.delete_image
        )

    def cleanup_ssh_key(self):
        self._cleanup(
            'ssh key',
            self.os_key,
            self.delete_keypair,
            self.os.delete_keypair
        )
        try:
            if self.delete_keypair and self.delete_instance:
                # self.wipe_private_key()
                pass
        except Exception as e:
            print("Error while clear up ssh key file: %s" % e)

    def cleanup(self):
        print("\nClearing up...")
        self.cleanup_instance()
        self.cleanup_ssh_key()
        self.cleanup_image()
        print("\nClearing done\n")

    def error_handler(self, signum, frame, timeout=True):
        if timeout:
            print("Timeout!")
        print("Clearing up due to error")
        self.cleanup()
        if timeout:
            raise TimeoutError("Timeout")

    def report_if_fail(self):
        if self.report and self.os_instance and not self.delete_instance:
            print("Instance %s is not removed. Please debug and remove it manually." % self.os_instance.id)
            print("Instance ip is %s" % self.ip)
            # print("Private key file is %s" % self.os_key_private_file)   ## A problem. Should fix this
        if self.report and self.os_image and not self.delete_image and not self.override_image:
            print("Image %s is not removed. Please debug and remove it manually." % self.os_image.id)

    def __enter__(self):
        self.connect()
        try:
            self.prepare()
            return self
        except Exception as e:
            print("Exception while preparing instance for test: %s" % e)
            print("Will print full trace after cleanup")
            self.cleanup()
            print("Continue tracing on original error: %s" % e)
            raise

    def __exit__(self, e_type, e_val, e_tb):
        "Cleaning up on exit"
        self.cleanup()
        self.report_if_fail()

    def get_env_config(self):
        flavor = self.flavor()
        env = {
            'instance_uuid': str(self.os_instance.id),
            'instance_name': str(self.instance_name).lower(),
            'flavor_id': str(self.flavor_id),
            'main_ip': str(self.ip),
            'ssh_private_key': str(self.os_key_private_file),
            'flavor_ram': str(flavor.ram),
            'flavor_name': str(flavor.name),
            'flavor_vcpus': str(flavor.vcpus),
            'flavor_disk': str(flavor.disk)
        }
        for num, ip in enumerate(self.ips()):
            env.update({'ip_' + str(num + 1): str(ip)})
        for num, iface in enumerate(self.network()):
            env.update({'iface_' + str(num + 1) + '_info': json.dumps(iface._info)})
        for meta_name, meta_value in flavor.get_keys().items():
            env.update({'flavor_meta_' + str(meta_name): str(meta_value)})
        return env

    def flavor(self):
        return self.os.get_flavor(self.flavor_id)

    def ips(self):
        result = []
        for ips in self.os_instance.networks.values():
            result.extend(ips)
        return result

    def network(self):
        return self.os_instance.interface_list()

    def wait_for_port(self, port=22, timeout=60):
        '''check if we can connect to given port. Wait for port
           up to timeout and then return error
        '''
        print("Waiting for instance to accept connections on %s:%s (time limit is %s s)" % (self.ip, port, timeout))
        start = time.time()
        while (start + timeout > time.time()):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)  # add source IP
            result = sock.connect_ex((self.ip, port))
            if result == 0:
                time.sleep(1)   # in many cases there is a race between port
                # become availabe and actual service been available
                print("Instance accepts connections on port %s" % port)
                return True
            time.sleep(3)
        print("Instance is not accepting connection on ip %s port %s." % (self.ip, port))
        return False
