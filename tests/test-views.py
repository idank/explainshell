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

    def test_explain_view_missing_query_params(self):
        rv = self.app.get('/explain')
        self.assertEqual(rv.status_code, 302)
        self.assertIn('You should be redirected automatically', rv.data)

    def test_missing_error(self):
        rv = self.app.get('/explain?cmd=zoomba -l')
        self.assertEqual(rv.status_code, 200)
        self.assertIn('No man page found for ', rv.data)

    def test_missing_error_json(self):
        rv = self.app.get('/api/explain?cmd=zoomba -l')
        self.assertEqual(rv.status_code, 200)
        data = json.loads(rv.data)
        self.assertEqual(data, {
            'e': "ProgramDoesNotExist(u'zoomba',)",
            'status': 'missingmanpage',
            'title': 'missing man page'})

    def test_explain_view_ok(self):
        rv = self.app.get('/explain?cmd=%s' % self.cmd)
        self.assertEqual(rv.status_code, 200)
        self.assertIn('<a href="/explain/1/bar">bar(1)</a></span>', rv.data)

    def test_explain_view_json_ok(self):
        rv = self.app.get('/api/explain?cmd=%s' % self.cmd)
        self.assertEqual(rv.status_code, 200)
        data = json.loads(rv.data)
        self.assertEqual(data, {
            'matches': [{
                'end': 3,
                'name': 'bar',
                'source': 'bar',
                'section': '1',
                'suggestions': [],
                'commandclass': 'command0 simplecommandstart',
                'start': 0,
                'helpHTML': 'bar synopsis',
                'spaces': ' ',
                'match': 'bar(1)'
            }, {
                'end': 10,
                'commandclass': 'command0',
                'start': 4,
                'helpHTML': '-a desc',
                'spaces': ' ',
                'match': '-a --a'
            }, {
                'end': 13,
                'commandclass': 'command0',
                'start': 11,
                'helpHTML': '-? help text',
                'spaces': '',
                'match': '-?'
            }]})

if __name__ == '__main__':
    unittest.main()
