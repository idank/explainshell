import json
import mock
import unittest

from explainshell.web import app
from tests import helpers

s = helpers.mockstore()


class FlaskrTestCase(unittest.TestCase):
    cmd = 'bar -a --a -?'

    def setUp(self):
        app.config['TESTING'] = True
        # mock store
        store_patcher = mock.patch('explainshell.store.store', return_value=s)
        store_patcher.start()
        self.addCleanup(store_patcher.stop)
        self.app = app.test_client()

    def test_explain_view_missing_cmd(self):
        rv = self.app.get('/explain')
        self.assertEqual(rv.status_code, 302)
        self.assertIn('You should be redirected automatically', rv.data)

    def test_explain_view_ok(self):
        rv = self.app.get('/explain?cmd=%s' % self.cmd)
        self.assertEqual(rv.status_code, 200)
        self.assertIn('<a href="/explain/1/bar">bar(1)</a></span>', rv.data)

    def test_explain_view_json_ok(self):
        rv = self.app.get('/api/explain?cmd=%s' % self.cmd)
        self.assertEqual(rv.status_code, 200)
        data = json.loads(rv.data)
        from pprint import pprint
        pprint(data)
        self.assertEqual(data, {
            'getargs': 'bar -a --a -?',
            'helptext': [['bar synopsis', 'help-0'],
                         ['-a desc', 'help-1'],
                         ['-? help text', 'help-2']],
            'matches': [{'commandclass': 'command0 simplecommandstart',
                         'end': 3,
                         'helpclass': 'help-0',
                         'match': 'bar(1)',
                         'name': 'bar',
                         'section': '1',
                         'source': 'bar',
                         'spaces': ' ',
                         'start': 0,
                         'suggestions': []},
                        {'commandclass': 'command0',
                         'end': 10,
                         'helpclass': 'help-1',
                         'match': '-a --a',
                         'spaces': ' ',
                         'start': 4},
                        {'commandclass': 'command0',
                         'end': 13,
                         'helpclass': 'help-2',
                         'match': '-?',
                         'spaces': '',
                         'start': 11}],
            'status': 'success'})

if __name__ == '__main__':
    unittest.main()
