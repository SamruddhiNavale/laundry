import base64
import importlib
import os
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from unittest.mock import Mock
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient


class LaundryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ['LAUNDRY_DB'] = os.path.join(self.tmp.name, 'test.db')
        import server
        self.server = importlib.reload(server)
        self.c = TestClient(self.server.app)
        with patch.dict(os.environ, {'LAUNDRY_ENABLE_LEGACY_SETUP': '1'}):
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

    def test_default_safeguards_and_private_validation_errors(self):
        with patch.dict(os.environ, {'LAUNDRY_ENABLE_LEGACY_SETUP': '0'}):
            self.assertEqual(self.c.post('/api/rooms/MAPLE1/set-initial-passcode', json={
                'passcode': 'anything',
            }).status_code, 404)
        page = self.c.get('/')
        self.assertIn("script-src 'self'", page.headers['Content-Security-Policy'])
        self.assertIn("frame-ancestors 'none'", page.headers['Content-Security-Policy'])
        self.assertEqual(page.headers['X-Content-Type-Options'], 'nosniff')
        invalid = self.c.put('/api/room/passcode', headers={
            'X-Room-Code': 'MAPLE1', 'X-Room-Token': 'fake-token',
        }, json={'current_passcode': 'test-current-private', 'passcode': {'value': 'test-new-private'}})
        self.assertEqual(invalid.status_code, 422)
        self.assertNotIn('test-current-private', invalid.text)
        self.assertNotIn('test-new-private', invalid.text)
        self.assertEqual(invalid.headers['Cache-Control'], 'no-store')

    def test_stale_owner_action_cannot_change_a_later_cycle(self):
        self.start(minutes=1)
        previous = {**self.a, 'X-Cycle-End': str(self.state()['end'])}
        self.assertEqual(self.post('finish', previous).status_code, 200)
        self.assertEqual(self.post('clear', previous).status_code, 200)
        self.start(minutes=2)
        self.assertEqual(self.post('finish', previous).status_code, 409)
        self.assertEqual(self.state()['state'], 'busy')
        self.assertEqual(self.post('finish').status_code, 200)
        self.assertEqual(self.post('clear', previous).status_code, 409)
        self.assertEqual(self.state()['state'], 'done')

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
        with patch.dict(os.environ, {'LAUNDRY_ENABLE_LEGACY_SETUP': '1'}):
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

    def test_remembered_room_access_is_scoped_to_the_joined_room(self):
        joined = self.c.post('/api/room/membership', headers={**self.a, 'X-Room-Code': 'MAPLE1'})
        self.assertEqual(joined.status_code, 200)
        remembered = {'X-Client-Id': 'alice', 'X-Room-Token': joined.json()['token']}
        self.assertEqual(self.c.get('/api/state', headers=remembered).status_code, 200)
        self.assertEqual(self.start(remembered).status_code, 200)
        other = self.c.post('/api/rooms', json={
            'name': 'Other house', 'washers': 1, 'dryers': 0, 'passcode': 'different123',
        }).json()['code']
        self.assertEqual(self.c.get('/api/state', headers={
            **remembered, 'X-Room-Code': other,
        }).status_code, 403)

    def test_leaving_revokes_the_token_and_clears_only_that_rooms_queue_spots(self):
        maple = self.c.post('/api/room/membership', headers={**self.b, 'X-Room-Code': 'MAPLE1'})
        other = self.c.post('/api/rooms', json={
            'name': 'Other house', 'washers': 1, 'dryers': 0, 'passcode': 'different123',
        }).json()
        maple_access = {'X-Room-Code': 'MAPLE1', 'X-Client-Id': 'bob', 'X-Room-Token': maple.json()['token']}
        other_access = {'X-Room-Code': other['code'], 'X-Room-Token': other['token'], 'X-Client-Id': 'bob'}
        other_machine = self.c.get('/api/state', headers=other_access).json()['machines'][0]['id']
        self.start()
        self.start({**other_access, 'X-Client-Id': 'alice'}, machine=other_machine)
        self.assertEqual(self.post('queue', maple_access).status_code, 200)
        self.assertEqual(self.post('queue', other_access, machine=other_machine).status_code, 200)
        self.assertEqual(self.c.delete('/api/room/membership', headers=maple_access).status_code, 200)
        self.assertEqual(self.c.get('/api/state', headers=maple_access).status_code, 403)
        self.assertEqual(self.state()['queue_length'], 0)
        other_state = self.c.get('/api/state', headers=other_access).json()['machines'][0]
        self.assertEqual(other_state['queue_position'], 1)
        joined_again = self.c.post('/api/room/membership', headers={
            'X-Room-Code': 'MAPLE1', 'X-Client-Id': 'bob',
        }, json={'passcode': 'maple123'})
        self.assertEqual(joined_again.status_code, 200)
        self.assertEqual(self.c.get('/api/state', headers={
            **maple_access, 'X-Room-Token': joined_again.json()['token'],
        }).status_code, 200)
        with self.server.connect() as db:
            self.assertIsNone(db.execute('SELECT 1 FROM left_rooms WHERE client=?', ('bob',)).fetchone())

    def test_create_and_join_passcode_combinations(self):
        valid = ('abc123', 'two words', ' leading and trailing ', 'été 🔑 123', 'a' * 128)
        invalid = ('short', 'a' * 129, ' ' * 8, 'abc\n123', 'abc\x00123')
        for index, passcode in enumerate(valid + invalid):
            with self.subTest(index=index):
                body = {'name': f'Room {index}', 'washers': 0, 'dryers': 1, 'passcode': passcode}
                created = self.c.post('/api/rooms', json=body)
                if passcode in invalid:
                    self.assertEqual(created.status_code, 422)
                    self.assertIsInstance(created.json()['detail'], str)
                    continue
                self.assertEqual(created.status_code, 200)
                code = created.json()['code']
                room = {'X-Room-Code': code}
                creator = self.c.get('/api/state', headers={
                    **room, 'X-Room-Token': created.json()['token'], 'X-Client-Id': 'creator',
                })
                self.assertEqual(creator.status_code, 200)
                joined = self.c.post('/api/room/membership', headers=room, json={'passcode': passcode})
                self.assertEqual(joined.status_code, 200)
                wrong = self.c.post('/api/room/membership', headers=room, json={'passcode': 'wrong one'})
                self.assertEqual(wrong.status_code, 403)
                state = self.c.get('/api/state', headers={
                    **room, 'X-Room-Token': joined.json()['token'], 'X-Client-Id': 'alice',
                })
                self.assertEqual(state.status_code, 200)
                self.assertEqual([machine['type'] for machine in state.json()['machines']], ['dryer'])

    def test_change_room_passcode_preserves_memberships_and_requires_current_passcode(self):
        room = {'X-Room-Code': 'MAPLE1'}
        token = self.c.post('/api/room/membership', headers=room, json={'passcode': 'maple123'}).json()['token']
        headers = {**room, 'X-Room-Token': token}
        change = {'current_passcode': 'maple123', 'passcode': 'new 🔑 code'}
        self.assertEqual(self.c.put('/api/room/passcode', headers={
            **room, 'X-Room-Token': 'invalid',
        }, json=change).status_code, 403)
        self.assertEqual(self.c.put('/api/room/passcode', headers=headers, json={
            **change, 'current_passcode': 'incorrect',
        }).status_code, 403)
        self.assertEqual(self.c.put('/api/room/passcode', headers=headers, json={
            **change, 'passcode': 'short',
        }).status_code, 422)
        self.assertEqual(self.c.put('/api/room/passcode', headers=headers, json=change).status_code, 200)
        self.assertEqual(self.c.post('/api/room/membership', headers=room, json={'passcode': 'maple123'}).status_code, 403)
        self.assertEqual(self.c.post('/api/room/membership', headers=room, json={'passcode': change['passcode']}).status_code, 200)
        self.assertEqual(self.c.get('/api/state', headers={**headers, 'X-Client-Id': 'alice'}).status_code, 200)

    def test_room_name_duplicates_on_create_and_rename(self):
        body = {'name': '  Café House  ', 'washers': 1, 'dryers': 0}
        first = self.c.post('/api/rooms', json={**body, 'passcode': 'coffee shop'})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()['name'], 'Café House')
        for name in ('Café House', 'CAFÉ HOUSE', 'cafe\u0301 house ', ' maple1 ', ' maple house '):
            with self.subTest(name=name):
                result = self.c.post('/api/rooms', json={
                    **body, 'name': name, 'passcode': 'coffee shop',
                })
                self.assertEqual(result.status_code, 200 if name.strip() == 'maple1' else 409)
                if result.status_code == 409:
                    self.assertIn('Choose a different room name', result.json()['detail'])
        rejected = self.c.put('/api/room', headers=self.a, json=body)
        self.assertEqual(rejected.status_code, 409)
        current = self.c.get('/api/state', headers=self.a).json()
        self.assertEqual(current['room']['name'], 'Maple House')
        self.assertEqual(len(current['machines']), 6)
        self.assertEqual(self.c.put('/api/room', headers=self.a, json={
            'name': 'MAPLE HOUSE', 'washers': 3, 'dryers': 3,
        }).status_code, 200)

    def test_simultaneous_duplicate_room_creation(self):
        def create(name):
            return self.c.post('/api/rooms', json={
                'name': name, 'washers': 1, 'dryers': 1, 'passcode': 'a secure code',
            }).status_code

        with ThreadPoolExecutor(max_workers=3) as pool:
            statuses = list(pool.map(create, ['Oak Hall', ' oak hall ', 'OAK HALL']))
        self.assertEqual(sorted(statuses), [200, 409, 409])

    def test_room_setup_field_boundaries(self):
        base = {'name': 'Boundary room', 'washers': 1, 'dryers': 1, 'passcode': 'room code'}
        for key, values in (
            ('name', ('', '   ', 'a' * 61, 'room\x00name')),
            ('washers', (-1, 51, 1.5, True, '1')),
            ('dryers', (-1, 51, 1.5, True, '1')),
        ):
            for value in values:
                with self.subTest(key=key, value=value):
                    self.assertEqual(self.c.post('/api/rooms', json={**base, key: value}).status_code, 422)
        for washers, dryers in ((0, 0), (0, 50), (50, 0), (50, 50)):
            with self.subTest(washers=washers, dryers=dryers):
                room = self.c.post('/api/rooms', json={
                    **base, 'name': f'Room {washers} {dryers}', 'washers': washers, 'dryers': dryers,
                })
                self.assertEqual(room.status_code, 200)
                state = self.c.get('/api/state', headers={
                    'X-Client-Id': 'alice', 'X-Room-Code': room.json()['code'],
                    'X-Room-Passcode': base['passcode'],
                }).json()
                self.assertEqual(sum(m['type'] == 'washer' for m in state['machines']), washers)
                self.assertEqual(sum(m['type'] == 'dryer' for m in state['machines']), dryers)

    def test_force_stop_releases_only_the_completed_cycle_and_preserves_fifo(self):
        self.start()
        self.post('queue', self.b)
        self.post('queue', self.d)
        release = {**self.d, 'X-Cycle-End': str(self.state()['end'])}
        self.assertEqual(self.post('force-stop', release).status_code, 409)
        self.post('finish')
        self.assertEqual(self.post('force-stop', {**release, 'X-Cycle-End': '1'}).status_code, 409)
        self.assertEqual(self.post('force-stop', release).status_code, 200)
        self.assertEqual(self.state(self.b)['queue_position'], 1)
        self.assertEqual(self.start(self.d).status_code, 409)
        self.assertEqual(self.start(self.b).status_code, 200)
        self.assertEqual(self.post('force-stop', release).status_code, 409)

    def test_background_push_sends_encrypted_reminder_and_stops_after_opt_out(self):
        joined = self.c.post('/api/room/membership', headers={**self.a, 'X-Room-Code': 'MAPLE1'})
        headers = {'X-Client-Id': 'alice', 'X-Room-Code': 'MAPLE1', 'X-Room-Token': joined.json()['token']}
        key = self.c.get('/api/push/key').json()['key']
        self.assertEqual(self.c.get('/api/push/key').json()['key'], key)
        device_key = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint,
        )
        subscription = {'endpoint': 'https://fcm.googleapis.com/fcm/send/test', 'keys': {
            'auth': base64.urlsafe_b64encode(b'a' * 16).decode().rstrip('='),
            'p256dh': base64.urlsafe_b64encode(device_key).decode().rstrip('='),
        }}
        self.assertEqual(self.c.put('/api/push/device', headers=headers, json=subscription).status_code, 200)
        with patch.object(self.server.time, 'time', return_value=10000):
            self.start(minutes=1)
        with patch.object(self.server.Session, 'post', return_value=Mock(status_code=201)) as send:
            with patch.object(self.server.time, 'time', return_value=10060):
                self.server.send_due_reminders()
                self.server.send_due_reminders()
                send.assert_called_once()
                self.assertEqual(send.call_args.args[0], subscription['endpoint'])
                request = send.call_args.kwargs
                self.assertIn('k=' + key, request['headers']['authorization'])
                self.assertEqual(request['headers']['content-encoding'], 'aes128gcm')
            self.assertEqual(self.c.delete('/api/push/device', headers=headers).status_code, 200)
            with patch.object(self.server.time, 'time', return_value=13660):
                self.server.send_due_reminders()
            send.assert_called_once()

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
