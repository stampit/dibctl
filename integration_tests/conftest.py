import logging
import vcr
import json
import pytest
import sys
import copy


class HappyVCR(object):

    def __init__(self):
        self.VCR = vcr.VCR(
            cassette_library_dir='cassettes/',
            record_mode='once',
            match_on=['uri', 'method', 'headers', 'body'],
            before_record_request=self.filter_request,
            before_record_response=self.filter_response
        )
        self.VCR.decode_compressed_response = True
        logging.basicConfig()
        vcr_log = logging.getLogger('vcr')
        vcr_log.setLevel(logging.DEBUG)
        ch = logging.FileHandler('/tmp/requests.log', mode='w')
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        ch.setFormatter(formatter)
        ch.setLevel(logging.DEBUG)
        vcr_log.addHandler(ch)
        vcr_log.info('Set up logging')
        self.log = vcr_log
        root = logging.getLogger()
        root.setLevel(logging.INFO)
        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(logging.INFO)
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        ch.setFormatter(formatter)
        root.addHandler(ch)
        self.count = 0

    def filter_request(self, request):
        # if 'pytest-filtered' in request.headers:
        #     self.log.info("repeated request to %s %s, ignoring" % (
        #         request.method,
        #         request.uri
        #     ))
        #     return request
        request = copy.deepcopy(request)
        request.add_header('pytest-filtered', 'true')
        self.log.info("filter request: %s: %s, %s, %s" % (
            id(request), self.count, request.method, request.uri
        ))
        if 'X-Auth-Token' in request.headers:
            self.log.info("old token %s" % request.headers['X-Auth-Token'])
            request.headers.pop('X-Auth-Token')
            self.log.info('Consealing X-Auth-Token header in %s (%s)' % (
                request.uri, id(request)
            ))
        request.headers.pop('x-distribution', None)
        if 'tokens' in request.uri and request.method == 'POST':
            self.log.info("%s: Token request detected", id(request))
            unsafe = json.loads(request.body)
            replacement = '{"tenantName": "pyvcr", ' + \
                '"passwordCredentials": ' + \
                '{"username": "username", "password": "password"}}'
            if 'auth' in unsafe:
                self.log.info("old creds %s" % str(unsafe['auth']))
                unsafe['auth'] = replacement
                safe = unsafe
            request.body = json.dumps(safe)
            self.log.info('Consealing request credentials in %s (%s)' % (
                request.uri, id(request)
            ))
        if 'images' in request.uri and request.method == 'POST':
            if len(request.body) > 256:
                self.log.info("Body is too large (%s bytes), truncating" % len(
                    request.body
                ))
                request.body = "'1f\r\nBody was too large, " + \
                    "truncated.\n\r\n0\r\n\r\n'"
        return request

    def filter_response(self, response):
        body = response['body']
        if 'string' in body:
            try:
                decoded_string = json.loads(body['string'])
            except:
                self.log.info("non-json body, ignoring")
                return response
            if 'access' in decoded_string:
                access = copy.deepcopy(decoded_string['access'])
                if 'token' in access:
                    access['token']['expires'] = '2038-01-15T16:17:18Z'
                    self.log.info("Patching token expiration date")
                    access['token']['id'] = 'consealed id'
                    self.log.info("Consealing token id")
                    access['token']['tenant']['description'] = ''
                    self.log.info("Consealing tenant description")
                    access['token']['tenant']['name'] = 'consealed name'
                    self.log.info("Consealing tenant name")
                if 'user' in access:
                    access['user']['username'] = 'consealed username'
                    self.log.info("Consealing username")
                    access['user']['username'] = 'consealed username'
                    self.log.info("Consealing username")
                    access['user']['name'] = 'consealed name'
                    self.log.info("Consealing user name")
                response['body']['string'] = json.dumps({'access': access})
            else:
                self.log.info("no access section in the body, ignoring")
        return response


@pytest.fixture(scope="function")
def happy_vcr(request):
    vcr = HappyVCR()
    return vcr.VCR.use_cassette