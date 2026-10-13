import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from team.launch import is_configured, wait_ready, main
from team.server import Store

class LauncherTests(unittest.TestCase):
    def test_read_only_configuration_probe(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'new.sqlite3'
            self.assertFalse(is_configured(p));self.assertFalse(p.exists())
            store=Store(p);self.assertFalse(is_configured(p))
            store.setup('test-password-long');self.assertTrue(is_configured(p))
    def test_health_checks_instance_not_just_port(self):
        response=MagicMock()
        response.__enter__.return_value.read.return_value=json.dumps({'service':'sff-team-calendar','instance':'other'}).encode()
        with patch('team.launch.urlopen',return_value=response),patch('team.launch.time.sleep'):
            self.assertFalse(wait_ready('http://localhost/', 'mine', attempts=1))
        response.__enter__.return_value.read.return_value=json.dumps({'service':'sff-team-calendar','instance':'mine'}).encode()
        with patch('team.launch.urlopen',return_value=response):
            self.assertTrue(wait_ready('http://localhost/', 'mine', attempts=1))
    def run_launch(self, root, passwords):
        server=MagicMock();server.instance='test-instance'
        with patch('team.launch.ROOT',root),patch('team.launch.socket.socket'),patch('team.launch.Server',return_value=server),patch('team.launch.threading.Thread'),patch('team.launch.wait_ready',return_value=True),patch('team.launch.webbrowser.open',side_effect=KeyboardInterrupt),patch('team.launch.getpass.getpass',side_effect=passwords) as prompt:
            result=main()
        return result,prompt.call_count,server
    def test_first_run_setup_and_existing_run_preserves_owner(self):
        with tempfile.TemporaryDirectory(prefix='SFF path with spaces ') as t:
            root=Path(t)
            code,count,server=self.run_launch(root,['only-test-password','only-test-password'])
            self.assertEqual((code,count),(0,2));server.shutdown.assert_called_once()
            p=root/'team-data'/'calendar.sqlite3';self.assertTrue(is_configured(p))
            with Store(p).connect() as db: original=Store.get(db,'password_hash')
            code,count,server=self.run_launch(root,[])
            self.assertEqual((code,count),(0,0))
            with Store(p).connect() as db:self.assertEqual(Store.get(db,'password_hash'),original)
    def test_occupied_port_does_not_prompt_or_open_browser(self):
        sock=MagicMock();sock.bind.side_effect=OSError('in use')
        with patch('team.launch.socket.socket',return_value=sock),patch('team.launch.getpass.getpass') as prompt,patch('team.launch.webbrowser.open') as browser:
            self.assertEqual(main(),1);prompt.assert_not_called();browser.assert_not_called()
    def test_password_mismatch_no_config(self):
        with tempfile.TemporaryDirectory() as t:
            with patch('team.launch.ROOT',Path(t)),patch('team.launch.socket.socket'),patch('team.launch.getpass.getpass',side_effect=['first-password-long','second-password-long']),patch('team.launch.webbrowser.open') as browser:
                self.assertEqual(main(),1);browser.assert_not_called()
            self.assertFalse(is_configured(Path(t)/'team-data'/'calendar.sqlite3'))

if __name__=='__main__':unittest.main()
