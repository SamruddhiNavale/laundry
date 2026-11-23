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
        self.a = self.join('alice')
        self.b = self.join('bob')
        self.d = self.join('dana')

    def join(self, client, code='MAPLE1'):
        headers = {'X-Client-Id': client, 'X-Room-Code': code}
        joined = self.c.post('/api/room/membership', headers=headers)
        self.assertEqual(joined.status_code, 200)
        return {**headers, 'X-Room-Token': joined.json()['token']}

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
        self.assertEqual(self.c.post('/api/rooms/MAPLE1/set-initial-passcode').status_code, 404)
        self.assertEqual(self.c.put('/api/room/passcode').status_code, 404)
        page = self.c.get('/')
        self.assertIn("script-src 'self'", page.headers['Content-Security-Policy'])
        self.assertIn("frame-ancestors 'none'", page.headers['Content-Security-Policy'])
        self.assertEqual(page.headers['X-Content-Type-Options'], 'nosniff')
        invalid = self.c.put('/api/room', headers=self.a, json={
            'name': {'value': 'private-validation-input'}, 'washers': 1, 'dryers': 1,
        })
        self.assertEqual(invalid.status_code, 422)
        self.assertNotIn('private-validation-input', invalid.text)
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

    def test_queue_turn_expires_and_gives_next_resident_a_full_hour(self):
        with patch.object(self.server.time, 'time', return_value=10000):
            self.start()
            self.post('queue', self.b)
            self.post('queue', self.d)
            self.assertIsNone(self.state(self.b)['turn_deadline'])
            self.post('finish')
            self.post('clear')
            self.assertEqual(self.state(self.b)['turn_deadline'], 13600)
        with patch.object(self.server.time, 'time', return_value=13599):
            self.assertEqual(self.state(self.b)['queue_position'], 1)
            self.assertEqual(self.start(self.d).status_code, 409)
        with patch.object(self.server.time, 'time', return_value=13600):
            self.assertEqual(self.start(self.b).status_code, 409)
            self.assertIsNone(self.state(self.b)['queue_position'])
            self.assertEqual(self.state(self.d)['queue_position'], 1)
            self.assertEqual(self.state(self.d)['turn_deadline'], 17200)
            stale_leave = {**self.b, 'X-Queue-Intent': 'leave'}
            self.assertEqual(self.post('queue', stale_leave).status_code, 200)
            self.assertIsNone(self.state(self.b)['queue_position'])
        with patch.object(self.server.time, 'time', return_value=17200):
            self.assertEqual(self.state()['queue_length'], 0)
            self.assertEqual(self.start().status_code, 200)

    def test_each_promoted_resident_gets_one_turn_push(self):
        with self.server.connect() as db:
            db.executemany('INSERT INTO push_devices(client,subscription) VALUES(?,?)',
                           [('bob', 'bob-subscription'), ('dana', 'dana-subscription')])
        with patch.object(self.server.time, 'time', return_value=10000), patch.object(self.server, 'deliver_push') as deliver:
            self.start()
            self.post('queue', self.b)
            self.post('queue', self.d)
            self.post('finish')
            self.server.send_turn_alerts()
            deliver.assert_not_called()
            self.post('clear')
            self.server.send_turn_alerts()
            self.server.send_turn_alerts()
            deliver.assert_called_once()
            self.assertEqual(deliver.call_args.args[0], 'bob-subscription')
            self.assertEqual(deliver.call_args.args[2]['kind'], 'turn')
        with patch.object(self.server.time, 'time', return_value=13600), patch.object(self.server, 'deliver_push') as deliver:
            self.server.send_turn_alerts()
            self.server.send_turn_alerts()
            deliver.assert_called_once()
            self.assertEqual(deliver.call_args.args[0], 'dana-subscription')
            self.assertEqual(deliver.call_args.args[2]['deadline'], 17200)

    def test_room_state_reports_if_the_reminder_worker_checked_recently(self):
        def worker_active():
            return self.c.get('/api/state', headers=self.a).json()['worker_active']

        with patch.object(self.server.time, 'time', return_value=10000):
            self.assertFalse(worker_active())
            self.server.record_worker_check()
            self.assertTrue(worker_active())
        with patch.object(self.server.time, 'time', return_value=10121):
            self.assertFalse(worker_active())

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
        data = {'name': 'Oak House', 'washers': 2, 'dryers': 1}
        created = self.c.post('/api/rooms', json=data)
        self.assertEqual(created.status_code, 200)
        code = created.json()['code']
        headers = {**self.a, 'X-Room-Code': code, 'X-Room-Token': created.json()['token']}
        room = self.c.get('/api/state', headers=headers).json()
        self.assertEqual(room['room']['name'], 'Oak House')
        self.assertEqual(len(room['machines']), 3)
        self.assertEqual(self.c.get('/api/state', headers={**self.a, 'X-Room-Code': 'NOPE'}).status_code, 403)
        self.assertEqual(self.c.get('/api/state', headers={**headers, 'X-Room-Token': 'wrong'}).status_code, 403)
        self.assertEqual(set(room['room']), {'code', 'name'})
        machine_id = room['machines'][1]['id']
        self.assertEqual(self.c.post(f'/api/machines/{machine_id}/start', headers=headers, json={'minutes': 5}).status_code, 200)
        self.assertEqual(self.c.post(f'/api/machines/{machine_id}/finish', headers=self.a).status_code, 404)
        self.assertEqual(self.c.put('/api/room', headers=headers, json={**data, 'washers': 1}).status_code, 409)
        self.assertEqual(self.c.put('/api/room', headers=headers, json={**data, 'washers': 3, 'dryers': 0}).status_code, 200)
        self.assertEqual(len(self.c.get('/api/state', headers=headers).json()['machines']), 3)
        self.assertEqual(len(self.c.get('/api/state', headers=self.a).json()['machines']), 6)

    def test_membership_token_required_for_all_room_operations(self):
        for headers in ({'X-Client-Id': 'alice'}, {'X-Client-Id': 'alice', 'X-Room-Token': 'wrong'}):
            self.assertEqual(self.c.get('/api/state', headers=headers).status_code, 403)
            self.assertEqual(self.c.post('/api/machines/1/start', headers=headers, json={'minutes': 1}).status_code, 403)
            self.assertEqual(self.c.put('/api/room', headers=headers, json={'name': 'test', 'washers': 1, 'dryers': 1}).status_code, 403)

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
            'name': 'Other house', 'washers': 1, 'dryers': 0,
        }).json()['code']
        self.assertEqual(self.c.get('/api/state', headers={
            **remembered, 'X-Room-Code': other,
        }).status_code, 403)

    def test_mobile_cookie_restores_membership_and_device_after_local_storage_loss(self):
        with TestClient(self.server.app, base_url='https://loop.test') as browser:
            room = browser.post('/api/rooms', json={
                'name': 'Recovery room', 'washers': 1, 'dryers': 0,
            }).json()
            headers = {'X-Room-Code': room['code'], 'X-Room-Token': room['token'], 'X-Client-Id': 'my-phone'}
            self.assertEqual(browser.post('/api/machines/' + str(
                browser.get('/api/state', headers=headers).json()['machines'][0]['id']
            ) + '/start', headers=headers, json={'minutes': 4}).status_code, 200)
            backup = browser.post('/api/room/saved', headers=headers)
            self.assertEqual(backup.status_code, 200)
            self.assertTrue(all('HttpOnly' in c and 'Secure' in c and 'Max-Age=' in c
                                for c in backup.headers.get_list('set-cookie')))
            restored = browser.get('/api/room/saved').json()
            self.assertEqual(restored['last'], room['code'])
            self.assertEqual(restored['client'], 'my-phone')
            self.assertEqual(restored['rooms'][0]['token'], room['token'])
            self.assertTrue(browser.get('/api/state', headers=headers).json()['machines'][0]['mine'])
            self.assertEqual(browser.delete('/api/room/membership', headers=headers).status_code, 200)
            self.assertEqual(browser.get('/api/room/saved').json()['rooms'], [])

    def test_leaving_revokes_the_token_and_clears_only_that_rooms_queue_spots(self):
        maple = self.c.post('/api/room/membership', headers={**self.b, 'X-Room-Code': 'MAPLE1'})
        other = self.c.post('/api/rooms', json={
            'name': 'Other house', 'washers': 1, 'dryers': 0,
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
        })
        self.assertEqual(joined_again.status_code, 200)
        self.assertEqual(self.c.get('/api/state', headers={
            **maple_access, 'X-Room-Token': joined_again.json()['token'],
        }).status_code, 200)
        with self.server.connect() as db:
            self.assertIsNone(db.execute('SELECT 1 FROM left_rooms WHERE client=?', ('bob',)).fetchone())

    def test_create_and_join_with_room_code_only(self):
        created = self.c.post('/api/rooms', json={'name': 'Room', 'washers': 0, 'dryers': 1})
        self.assertEqual(created.status_code, 200)
        room = created.json()
        self.assertRegex(room['code'], r'^[A-F0-9]{16}$')
        self.assertEqual(self.c.post('/api/room/membership', headers={'X-Room-Code': 'unknown'}).status_code, 404)
        self.assertEqual(self.c.post('/api/room/membership', headers={'X-Room-Code': 'invalid!'}).status_code, 422)
        joined = self.join('visitor', room['code'].lower())
        state = self.c.get('/api/state', headers=joined).json()
        self.assertEqual([machine['type'] for machine in state['machines']], ['dryer'])

    def test_existing_rooms_and_tokens_survive_passcode_removal(self):
        with self.server.connect() as db:
            db.execute('ALTER TABLE rooms ADD COLUMN passcode_hash TEXT')
            db.execute("UPDATE rooms SET passcode_hash='legacy-value' WHERE code='MAPLE1'")
        self.start()
        self.server.initialize()
        self.assertTrue(self.state()['mine'])
        self.assertEqual(self.c.get('/api/state', headers=self.join('new-resident')).status_code, 200)

    def test_room_name_duplicates_on_create_and_rename(self):
        body = {'name': '  Café House  ', 'washers': 1, 'dryers': 0}
        first = self.c.post('/api/rooms', json={**body})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()['name'], 'Café House')
        for name in ('Café House', 'CAFÉ HOUSE', 'cafe\u0301 house ', ' maple1 ', ' maple house '):
            with self.subTest(name=name):
                result = self.c.post('/api/rooms', json={
                    **body, 'name': name,
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
                'name': name, 'washers': 1, 'dryers': 1,
            }).status_code

        with ThreadPoolExecutor(max_workers=3) as pool:
            statuses = list(pool.map(create, ['Oak Hall', ' oak hall ', 'OAK HALL']))
        self.assertEqual(sorted(statuses), [200, 409, 409])

    def test_room_setup_field_boundaries(self):
        base = {'name': 'Boundary room', 'washers': 1, 'dryers': 1}
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
                    'X-Room-Token': room.json()['token'],
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
