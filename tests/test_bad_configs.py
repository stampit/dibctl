#!/usr/bin/python
import os
import inspect
import pytest
import mock
from mock import sentinel


@pytest.fixture
def config_module():
    from dibctl import config
    return config


def gather_configs(ctype):
    PATH = 'bad_configs'
    ourfilename = os.path.abspath(inspect.getfile(inspect.currentframe()))
    currentdir = os.path.dirname(ourfilename)
    for f in os.listdir(os.path.join(currentdir, PATH)):
        if not f.endswith(ctype + '.yaml'):
            continue
        yield os.path.join(currentdir, PATH, f)


@pytest.mark.parametrize('conf', gather_configs('image'))
def test_image_conf(config_module, conf):
    with pytest.raises(config_module.InvaidConfigError):
        config_module.ImageConfig(config_file=conf)


@pytest.mark.parametrize('conf', gather_configs('test'))
def test_test_conf(conf):
    assert conf


@pytest.mark.parametrize('conf', gather_configs('upload'))
def test_upload_conf(conf):
    assert conf
