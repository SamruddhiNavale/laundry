import importlib
import os
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from unittest.mock import Mock
from fastapi.testclient import TestClient


class LaundryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ['LAUNDRY_DB'] = os.path.join(self.tmp.name, 'test.db')
        import server
        self.server = importlib.reload(server)
        self.c = TestClient(self.server.app)
        self.c.post('/api/rooms/MAPLE1/set-initial-passcode', json={'passcode': 'maple123'})
        self.a = {'X-Client-Id': 'alice', 'X-Room-Passcode': 'maple123'}
        self.b = {'X-Client-Id': 'bob', 'X-Room-Passcode': 'maple123'}
        self.d = {'X-Client-Id': 'dana', 'X-Room-Passcode': 'maple123'}

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def post(self, action, user=None, machine=1, **kwargs):
        return self.c.post(f'/api/machines/{machine}/{action}', headers=user or self.a, **kwargs)

    def state(self, user=None, machine=1):
        return self.c.get('/api/state', headers=user or self.a).json()['machines'][machine-1]

    def start(self, user=None, machine=1, minutes=30):
        return self.post('start', user, machine, json={'minutes': minutes})

    def test_validation_and_initial_state(self):
        self.assertEqual(len(self.c.get('/api/state', headers=self.a).json()['machines']), 6)
        self.assertEqual(self.c.get('/api/state').status_code, 400)
        self.assertEqual(self.c.get('/api/state', headers={'X-Client-Id': 'bad id'}).status_code, 400)
        for value in (0, -1, 241, 1.5, 'abc', True):
            with self.subTest(value=value): self.assertEqual(self.start(minutes=value).status_code, 422)
        self.assertEqual(self.post('start').status_code, 422)
        self.assertEqual(self.post('start', machine=99, json={'minutes': 1}).status_code, 404)
        self.assertEqual(self.post('unknown').status_code, 404)
        self.assertEqual(self.c.get('/laundry.db').status_code, 404)

    def test_states_and_owner_restrictions(self):
        for action in ('finish', 'clear', 'remind', 'still-there'):
            self.assertEqual(self.post(action).status_code, 403)
        self.assertEqual(self.start().status_code, 200)
        self.assertTrue(self.state()['mine'])
        self.assertFalse(self.state(self.b)['mine'])
        self.assertNotIn('next_reminder', self.state(self.b))
        self.assertEqual(self.start(self.b).status_code, 409)
        self.assertEqual(self.post('queue').status_code, 409)
        for action in ('finish', 'clear', 'remind', 'still-there'):
            self.assertEqual(self.post(action, self.b).status_code, 403)
        self.assertEqual(self.post('finish').status_code, 200)
        self.assertEqual(self.state()['state'], 'done')
        self.assertEqual(self.post('clear').status_code, 200)
        self.assertEqual(self.state()['state'], 'free')

    def test_fifo_queue_and_leaving(self):
        self.start()
        self.assertEqual(self.post('queue', self.b).status_code, 200)
        self.assertEqual(self.post('queue', self.d).status_code, 200)
        self.assertEqual(self.state(self.b)['queue_position'], 1)
        self.assertEqual(self.state(self.d)['queue_position'], 2)
        self.post('finish'); self.post('clear')
        self.assertEqual(self.start(self.d).status_code, 409)
        self.assertEqual(self.post('queue', self.b).status_code, 200)
        self.assertEqual(self.state(self.d)['queue_position'], 1)
        self.assertEqual(self.start(self.d).status_code, 200)
        self.assertEqual(self.state(self.d)['queue_length'], 0)

    def test_reminder_timing_and_yes_no(self):
        with patch.object(self.server.time, 'time', return_value=10000):
            self.start(minutes=1)
        with patch.object(self.server.time, 'time', return_value=10059):
            self.assertEqual(self.state()['state'], 'busy')
        with patch.object(self.server.time, 'time', return_value=10060):
            self.assertEqual(self.state()['state'], 'done')
            self.assertEqual(self.post('remind').status_code, 200)
            self.assertEqual(self.post('remind').status_code, 409)
            self.assertEqual(self.post('still-there').status_code, 200)
        with patch.object(self.server.time, 'time', return_value=13659):
            self.assertEqual(self.post('remind').status_code, 409)
        with patch.object(self.server.time, 'time', return_value=13660):
            self.assertEqual(self.post('remind').status_code, 200)
            self.assertEqual(self.post('clear').status_code, 200)
            self.assertEqual(self.post('remind').status_code, 403)

    def test_parallel_claims(self):
        def start(user): return self.start(user).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(start, [self.a, self.b]))
        self.assertEqual(sorted(results), [200, 409])

    def test_rooms_and_resize(self):
        data = {'name': 'Oak House', 'washers': 2, 'dryers': 1, 'passcode': 'oakpass'}
        created = self.c.post('/api/rooms', json=data)
        self.assertEqual(created.status_code, 200)
        code = created.json()['code']
        headers = {**self.a, 'X-Room-Code': code, 'X-Room-Passcode': 'oakpass'}
        room = self.c.get('/api/state', headers=headers).json()
        self.assertEqual(room['room']['name'], 'Oak House')
        self.assertEqual(len(room['machines']), 3)
        self.assertEqual(self.c.get('/api/state', headers={**self.a, 'X-Room-Code': 'NOPE'}).status_code, 403)
        self.assertEqual(self.c.get('/api/state', headers={**headers, 'X-Room-Passcode': 'wrong'}).status_code, 403)
        self.assertNotIn('passcode_hash', room['room'])
        self.assertEqual(self.c.post(f'/api/rooms/{code}/set-initial-passcode', json={'passcode': 'hijack'}).status_code, 409)
        machine_id = room['machines'][1]['id']
        self.assertEqual(self.c.post(f'/api/machines/{machine_id}/start', headers=headers, json={'minutes': 5}).status_code, 200)
        self.assertEqual(self.c.post(f'/api/machines/{machine_id}/finish', headers=self.a).status_code, 404)
        self.assertEqual(self.c.put('/api/room', headers=headers, json={**data, 'washers': 1}).status_code, 409)
        self.assertEqual(self.c.put('/api/room', headers=headers, json={**data, 'washers': 3, 'dryers': 0}).status_code, 200)
        self.assertEqual(len(self.c.get('/api/state', headers=headers).json()['machines']), 3)
        self.assertEqual(len(self.c.get('/api/state', headers=self.a).json()['machines']), 6)

    def test_passcode_required_for_all_room_operations(self):
        for headers in ({'X-Client-Id': 'alice'}, {'X-Client-Id': 'alice', 'X-Room-Passcode': 'wrong'}):
            self.assertEqual(self.c.get('/api/state', headers=headers).status_code, 403)
            self.assertEqual(self.c.post('/api/machines/1/start', headers=headers, json={'minutes': 1}).status_code, 403)
            self.assertEqual(self.c.put('/api/room', headers=headers, json={'name': 'test', 'washers': 1, 'dryers': 1}).status_code, 403)
        self.assertEqual(self.c.post('/api/rooms', json={'name': 'test', 'washers': 1, 'dryers': 1, 'passcode': 'short'}).status_code, 422)

    def test_each_machine_and_boundary_durations(self):
        for machine in range(1, 7):
            with self.subTest(machine=machine):
                self.assertEqual(self.start(machine=machine, minutes=1 if machine % 2 else 240).status_code, 200)
                self.assertEqual(self.post('finish', machine=machine).status_code, 200)
                self.assertEqual(self.post('clear', machine=machine).status_code, 200)

    def test_resize_rolls_back_when_any_removed_machine_is_busy(self):
        self.start(machine=6)
        result = self.c.put('/api/room', headers=self.a, json={'name': 'Changed', 'washers': 1, 'dryers': 1})
        self.assertEqual(result.status_code, 409)
        state = self.c.get('/api/state', headers=self.a).json()
        self.assertEqual(state['room']['name'], 'Maple House')
        self.assertEqual(len(state['machines']), 6)

    def test_queue_toggle_and_reserved_access(self):
        self.start()
        self.post('queue', self.b)
        self.post('queue', self.d)
        self.post('queue', self.b)
        self.post('queue', self.b)
        self.assertEqual(self.state(self.b)['queue_position'], 2)
        self.post('finish'); self.post('clear')
        self.assertEqual(self.start(self.b).status_code, 409)
        self.assertEqual(self.post('queue', self.d).status_code, 200)
        self.assertEqual(self.start(self.b).status_code, 200)

    def test_simultaneous_reminder_claim(self):
        self.start(); self.post('finish')
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.post('remind').status_code, range(2)))
        self.assertEqual(sorted(results), [200, 409])

    def test_join_behind_reservation_and_only_owner_can_finish(self):
        self.assertEqual(self.start().status_code, 200)
        self.assertEqual(self.post('queue', self.b).status_code, 200)
        self.assertEqual(self.post('finish', self.b).status_code, 403)
        self.assertEqual(self.post('finish').status_code, 200)
        self.assertEqual(self.post('clear', self.b).status_code, 403)
        self.assertEqual(self.post('clear').status_code, 200)
        self.assertEqual(self.post('queue', self.d).status_code, 200)
        self.assertEqual(self.state(self.d)['queue_position'], 2)
        self.assertEqual(self.start(self.d).status_code, 409)
        self.assertEqual(self.start(self.b).status_code, 200)
        self.assertEqual(self.state(self.d)['queue_position'], 1)

    def test_postgres_query_translation_and_lock(self):
        db = self.server.PostgresConnection.__new__(self.server.PostgresConnection)
        db.connection = Mock()
        db.execute('BEGIN IMMEDIATE')
        db.connection.execute.assert_called_with('SELECT pg_advisory_xact_lock(492031)')
        db.execute('UPDATE machines SET end=? WHERE id=?', (123, 4))
        db.connection.execute.assert_called_with('UPDATE machines SET "end"=%s WHERE id=%s', (123, 4))

    def test_vercel_requires_external_database(self):
        with patch.object(self.server, 'DATABASE_URL', None), patch.dict(os.environ, {'VERCEL': '1'}):
            with self.assertRaisesRegex(RuntimeError, 'Postgres'):
                self.server.connect()

if __name__ == '__main__': unittest.main()
