const params = new URLSearchParams(location.search);
const roomParam = params.get('room')?.trim().toUpperCase();
const pickupFromPush = params.get('pickup');
const pickupCycleEnd = Number(params.get('end'));
const nativeFetch = window.fetch.bind(window);
let roomCode = '';
let roomToken = '';
let roomVersion = 0;
let roomAccessRejected = false;

function roomRequest(url, options = {}) {
  return nativeFetch(url, {
    ...options,
    headers: {
      ...options.headers,
      'X-Client-Id': client,
      'X-Room-Code': roomCode,
      'X-Room-Token': roomToken,
    },
  });
}

const clientKey='loop-client-id';let client=localStorage.getItem(clientKey);if(!client){client=crypto.randomUUID();localStorage.setItem(clientKey,client)}
let machines=[],filter='all',pushRegistered=false;const $=s=>document.querySelector(s);
function toast(message){const t=$('#toast');t.textContent=message;t.classList.add('show');clearTimeout(toast.timer);toast.timer=setTimeout(()=>t.classList.remove('show'),4000)}
async function refresh() {
  const version = roomVersion;
  try {
    const res = await roomRequest('/api/state', {cache: 'no-store'});
    if (version !== roomVersion) return false;
    if (res.status === 403) {
      // A deployment can temporarily point at a database that doesn't recognize this token.
      roomAccessRejected = true;
      roomVersion++;
      roomToken = '';
      showEntry('This room is currently unavailable. You can retry with your saved access.');
      return false;
    }
    if (!res.ok) throw Error('Unable to load machine status');
    const data = await res.json();
    if (version !== roomVersion) return false;
    machines = data.machines;
    pushRegistered = data.push_registered;
    updateRoom(data.room);
    for (const m of machines) {
      const key = `loop-turn-${roomCode}-${m.id}`;
      if (m.state === 'free' && m.queue_position === 1) {
        if (!localStorage.getItem(key)) {
          localStorage.setItem(key, 'shown');
          const body = 'It’s your turn. Start your load when you’re ready.';
          toast(body);
          if (alertsEnabled() && window.Notification?.permission === 'granted') {
            registration.then(worker => worker?.showNotification(`${m.name} is available`, {
              body, icon: '/icon-192.png', data: {room: data.room.code},
            })).catch(console.warn);
          }
        }
      } else {
        localStorage.removeItem(key);
      }
    }
    render();
    checkReminders();
    return true;
  } catch (error) {
    if (version === roomVersion) toast(error.message);
    return false;
  }
}
function render(){for(const type of ['washer','dryer'])$(`#${type==='washer'?'wash':'dry'}-count`).innerHTML=`${machines.filter(m=>m.type===type&&m.state==='free'&&!m.queue_length).length} <span>/ ${machines.filter(m=>m.type===type).length}</span>`;const queued=machines.filter(m=>m.queue_position);$('#queue-count').textContent=queued.length;$('#queue-badge').textContent=`${queued.length} waiting`;
$('#machine-grid').innerHTML=machines.filter(m=>filter==='all'||m.type===filter).map(m=>{const minutes=Math.max(1,Math.ceil((m.end*1000-Date.now())/60000));const reserved=m.state==='free'&&m.queue_length;const detail=m.state==='free'?(reserved?'Reserved for the next person':'Ready for your next load'):m.state==='done'?`Cycle finished · ${m.mine?'your clothes':'clothes may remain'}`:`${m.mine?'Your cycle':'In use'} · <strong>~${minutes} min left</strong>`;let button=m.state==='free'&&(!reserved||m.queue_position===1)?`<button data-action="start" data-id="${m.id}">Start a load ↗</button>`:m.mine&&m.state==='done'?`<button data-action="clear" data-id="${m.id}">Clothes picked up ✓</button>`:m.mine?`<button class="secondary" data-action="finish" data-id="${m.id}">End cycle</button>`:m.state==='free'&&m.queue_position?`<span>In queue · position ${m.queue_position}</span>`:`<button class="secondary" data-action="queue" data-id="${m.id}">${m.queue_position?'Leave queue':'Join queue'} ${m.queue_position?'−':'+'}</button>`;return `<article class="machine ${m.type}"><div class="card-top"><span class="machine-type">${m.type.toUpperCase()}</span><span class="status ${m.state==='busy'?'busy':m.state==='done'?'done':''}">● ${m.state==='free'?(reserved?'Reserved':'Available'):m.state==='done'?'Cycle complete':'In use'}</span></div><div class="machine-body"><div class="machine-illustration"></div><div><h3>${m.name}</h3><div class="machine-detail">${detail}</div></div></div><div class="card-bottom"><span>${m.queue_length?`${m.queue_length} in queue`:m.state==='free'?'Ready to go':m.state==='done'?'Waiting for pickup':'Running now'}</span><div class="machine-actions">${button}${m.state==='done'&&!m.mine?`<button class="force-stop" data-action="force-stop" data-id="${m.id}">Force stop</button>`:''}</div></div></article>`}).join('');
$('#queue-list').innerHTML=queued.length?queued.map(m=>`<div class="queue-row"><div><strong>${m.name}</strong><small>${m.state==='free'&&m.queue_position===1?'Available now · your turn':`Position ${m.queue_position} in line`}</small></div><button data-action="queue" data-id="${m.id}">Leave queue</button></div>`).join(''):'<div class="empty">No waiting around here. Join a queue on a busy machine and your spot will show up here.</div>'}
document.addEventListener('click',async e=>{const f=e.target.closest('[data-filter]');if(f){filter=f.dataset.filter;document.querySelectorAll('[data-filter]').forEach(b=>b.classList.toggle('selected',b===f));render();return}const b=e.target.closest('[data-action]');if(!b)return;const m=machines.find(x=>x.id===Number(b.dataset.id));if(!m)return;const version=roomVersion;
if(b.dataset.action==='force-stop'&&!window.confirm(`Release ${m.name}? Check that the cycle is finished and the drum is empty first. The queue will be kept.`))return;
let body;if(b.dataset.action==='start'){const raw=await cyclePrompt(`How many minutes will ${m.name} run?`,m.type==='washer'?'35':'45');if(raw===null)return;const minutes=Number(raw);if(!Number.isInteger(minutes)||minutes<1||minutes>240){toast('Enter a whole number between 1 and 240 minutes.');return}body=JSON.stringify({minutes})}if(version!==roomVersion)return;b.disabled=true;try{const res=await roomRequest(`/api/machines/${m.id}/${b.dataset.action}`,{method:'POST',headers:{'X-Client-Id':client,'Content-Type':'application/json',...(['finish','clear','force-stop'].includes(b.dataset.action)?{'X-Cycle-End':String(m.end)}:{})},body});const result=await res.json();if(!res.ok)throw Error(result.detail||'Request failed');if(b.dataset.action!=='finish')toast(['clear','force-stop'].includes(b.dataset.action)?`${m.name} was released.`:'Your update was saved.');await refresh()}catch(e){toast(e.message);await refresh()}});
const alertsButton = $('#notifications');
const registration = 'serviceWorker' in navigator
  ? navigator.serviceWorker.register('/sw.js').then(() => navigator.serviceWorker.ready).catch(() => null)
  : Promise.resolve(null);
const supportsPush = 'PushManager' in window && 'Notification' in window && 'serviceWorker' in navigator;
const alertsEnabled = () => localStorage.getItem('loop-alerts') !== 'off';

function updateAlerts(status) {
  const enabled = alertsEnabled();
  alertsButton.setAttribute('aria-pressed', String(enabled));
  alertsButton.textContent = `Notifications ${enabled ? 'on' : 'off'}`;
  $('#enable-alerts').hidden = !enabled || (pushRegistered && !status) || !supportsPush || Notification.permission === 'denied';
  $('#notification-status').textContent = status || (!enabled ? 'Notifications are off on this device.'
    : !supportsPush ? 'To get mobile alerts, open Loop from your home screen on a browser that supports Web Push.'
    : Notification.permission === 'denied' ? 'Allow notifications in your device settings to receive alerts.'
    : pushRegistered ? 'Pickup reminders can arrive while Loop is closed.'
    : 'Tap Allow notifications to receive reminders while Loop is closed.');
}

let alertSync = Promise.resolve();
function syncAlerts() {
  alertSync = alertSync.catch(() => {}).then(async () => {
    if (!roomToken || !supportsPush) return;
    const worker = await registration;
    if (!worker || !roomToken) return;
    let subscription = await worker.pushManager.getSubscription();
    if (!alertsEnabled() || Notification.permission !== 'granted') {
      if (subscription) await subscription.unsubscribe();
      const response = await roomRequest('/api/push/device', {method: 'DELETE'});
      if (!response.ok) throw Error('Couldn’t update notification settings.');
      pushRegistered = false;
      return;
    }
    if (!subscription) {
      const response = await nativeFetch('/api/push/key');
      if (!response.ok) throw Error('Couldn’t set up notifications.');
      const {key} = await response.json();
      const padded = key.replace(/-/g, '+').replace(/_/g, '/').padEnd(Math.ceil(key.length / 4) * 4, '=');
      subscription = await worker.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: Uint8Array.from(atob(padded), c => c.charCodeAt(0)),
      });
    }
    const response = await roomRequest('/api/push/device', {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(subscription),
    });
    if (!response.ok) throw Error('Couldn’t register this device for notifications.');
    pushRegistered = true;
  });
  return alertSync.then(() => updateAlerts()).catch(error => updateAlerts(error.message));
}

async function changeAlerts(enabled) {
  alertsButton.disabled = $('#enable-alerts').disabled = true;
  localStorage.setItem('loop-alerts', enabled ? 'on' : 'off');
  try {
    if (enabled && supportsPush && Notification.permission === 'default') {
      await Notification.requestPermission();
    }
    await syncAlerts();
  } catch {
    updateAlerts('Couldn’t enable device notifications.');
  } finally {
    alertsButton.disabled = $('#enable-alerts').disabled = false;
  }
}
alertsButton.onclick = () => changeAlerts(!alertsEnabled());
$('#enable-alerts').onclick = () => changeAlerts(true);
window.addEventListener('storage', event => {
  if (event.key === 'loop-alerts') syncAlerts();
});
setInterval(() => { if (roomToken) refresh(); }, 5000);
function cyclePrompt(label, initial){return new Promise(resolve=>{const d=document.createElement('dialog');d.innerHTML='<form><label></label><input type="number" min="1" max="240" step="1" required><button type="button">Cancel</button> <button type="submit">Start load</button></form>';const form=d.querySelector('form');form.querySelector('label').textContent=label;form.querySelector('input').value=initial;document.body.append(d);let done=false;function finish(value){if(done)return;done=true;d.close();d.remove();resolve(value)}form.querySelector('button[type=button]').onclick=()=>finish(null);d.addEventListener('cancel',e=>{e.preventDefault();finish(null)});form.onsubmit=e=>{e.preventDefault();finish(form.querySelector('input').value)};d.showModal()})}
let reminderOpen = false;
let pushPickupPending = Boolean(pickupFromPush);
async function checkReminders() {
  if (reminderOpen || !alertsEnabled() || document.visibilityState !== 'visible') return;
  const fromPush = pushPickupPending && roomCode === roomParam;
  const m = fromPush
    ? machines.find(x => x.id === Number(pickupFromPush) && x.end === pickupCycleEnd && x.mine && x.state === 'done')
    : !pushRegistered && machines.find(x => x.mine && x.state === 'done' && (!x.next_reminder || x.next_reminder * 1000 <= Date.now()));
  if (fromPush) pushPickupPending = false;
  if (!m) return;
  reminderOpen = true;
  const version = roomVersion;
  try {
    if (!fromPush) {
      const claim = await roomRequest(`/api/machines/${m.id}/remind`, {method: 'POST'});
      if (!claim.ok || version !== roomVersion) return;
    }
    const answer = await pickupPrompt(m.name);
    if (version !== roomVersion) return;
    const action = answer === 'yes' ? 'clear' : 'still-there';
    const res = await roomRequest(`/api/machines/${m.id}/${action}`, {
      method: 'POST', headers: {'X-Cycle-End': String(m.end)},
    });
    if (!res.ok) throw Error('This pickup response could not be saved.');
    toast(answer === 'yes' ? `${m.name} was released.` : 'We’ll remind you again in an hour.');
    await refresh();
  } catch (error) {
    toast(error.message);
  } finally {
    reminderOpen = false;
  }
}
function pickupPrompt(name){return new Promise(resolve=>{const d=document.createElement('dialog');d.innerHTML='<form><h2>Laundry is ready</h2><p class="pickup-message"></p><button type="button" data-answer="no">No, still there</button> <button type="button" data-answer="yes">Yes, removed</button></form>';d.querySelector('p').textContent=`Have you removed your clothes from ${name}? If not, we’ll ask again in one hour.`;document.body.append(d);let done=false;function finish(answer){if(done)return;done=true;d.close();d.remove();resolve(answer)}d.querySelectorAll('[data-answer]').forEach(b=>b.onclick=()=>finish(b.dataset.answer));d.addEventListener('cancel',e=>{e.preventDefault();finish('no')});d.showModal()})}
function updateRoom(room) {
  document.querySelectorAll('.building strong,.date-card strong').forEach(element => element.textContent = room.name);
  $('.breadcrumb').textContent = room.name + ' / Laundry room';
  const key = 'loop-room-name-' + room.code;
  if (localStorage.getItem(key) !== room.name) {
    localStorage.setItem(key, room.name);
    updateRoomPicker();
  }
}

const roomControls = document.createElement('div');
roomControls.className = 'room-controls';
roomControls.innerHTML = '<button id="edit-room">Edit room</button><button id="change-passcode">Change room passcode</button><button id="invite-room">Invite residents</button>';
$('.section-head').append(roomControls);
const roomPicker = $('.room-picker');
const roomOptions = $('#room-picker-options');

const compactRoomMenu = window.matchMedia('(max-width: 900px)');
function placeRoomMenu() {
  $(compactRoomMenu.matches ? '.header-left' : '#sidebar-room').append(roomPicker);
}
compactRoomMenu.addEventListener('change', placeRoomMenu);
placeRoomMenu();

function savedRooms() {
  return Object.keys(localStorage)
    .filter(key => key.startsWith('loop-token-') && localStorage.getItem(key))
    .map(key => key.slice('loop-token-'.length))
    .map(code => ({code, name: localStorage.getItem('loop-room-name-' + code) || code}))
    .sort((a, b) => a.name.localeCompare(b.name) || a.code.localeCompare(b.code));
}

function updateRoomPicker() {
  const rooms = savedRooms();
  const current = Array.from(roomOptions.querySelectorAll('button'));
  if (current.length !== rooms.length || rooms.some(({code, name}, i) =>
    current[i].value !== code || current[i].textContent !== name)) {
    roomOptions.replaceChildren(...rooms.map(({code, name}) => {
      const item = document.createElement('li');
      const button = document.createElement('button');
      button.type = 'button';
      button.value = code;
      button.textContent = name;
      item.append(button);
      return item;
    }));
  }
  for (const button of roomOptions.querySelectorAll('button')) {
    if (button.value === roomCode) button.setAttribute('aria-current', 'true');
    else button.removeAttribute('aria-current');
  }
}

window.addEventListener('storage', event => {
  if (!event.key || event.key.startsWith('loop-token-') || event.key.startsWith('loop-room-name-')) {
    updateRoomPicker();
  }
});

roomOptions.onclick = async event => {
  const button = event.target.closest('button');
  if (!button || accessOpen) return;
  roomPicker.open = false;
  roomPicker.querySelector('summary').focus();
  if (button.value !== roomCode) await showAccess(button.value);
  updateRoomPicker();
};
$('#leave-room').onclick = async () => {
  if (accessOpen || !roomToken) return;
  const code = roomCode;
  const name = $('.building strong').textContent;
  if (!window.confirm(`Leave ${name}? Your spots in this room’s queues will be released.`)) return;
  roomPicker.open = false;
  accessOpen = true;
  try {
    const response = await roomRequest('/api/room/membership', {method: 'DELETE'});
    if (!response.ok && response.status !== 403) throw Error('Couldn’t leave this room. Please try again.');
    localStorage.removeItem('loop-token-' + code);
    localStorage.removeItem('loop-room-name-' + code);
    if (localStorage.getItem('loop-room') === code) localStorage.removeItem('loop-room');
    sessionStorage.removeItem('loop-pass-' + code);

    const next = savedRooms()[0];
    if (next) {
      await openRememberedRoom(next.code);
    } else {
      roomVersion++;
      roomCode = '';
      roomToken = '';
      machines = [];
      history.replaceState(null, '', '/?home=1');
      showEntry(`You left ${name}.`);
    }
  } catch (error) {
    toast(error.message);
  } finally {
    accessOpen = false;
    updateRoomPicker();
  }
};

function formDialog(title, fields, errorMessage = ''){return new Promise(resolve=>{const d=document.createElement('dialog');const form=document.createElement('form');const heading=document.createElement('h2');heading.textContent=title;form.append(heading);if(errorMessage){const error=document.createElement('p');error.setAttribute('role','alert');error.textContent=errorMessage;form.append(error)}for(const field of fields){const label=document.createElement('label');label.textContent=field.label;const input=document.createElement('input');input.name=field.name;input.type=field.type||'text';input.value=field.value??'';input.required=true;if(input.type==='number'){input.min='0';input.max='50';input.step='1'}label.append(input);form.append(label)}const cancel=document.createElement('button');cancel.type='button';cancel.textContent='Cancel';const submit=document.createElement('button');submit.textContent='Continue';submit.type='submit';form.append(cancel,submit);d.append(form);document.body.append(d);let finished=false;function finish(data){if(finished)return;finished=true;d.close();d.remove();resolve(data)}cancel.onclick=()=>finish(null);d.addEventListener('cancel',e=>{e.preventDefault();finish(null)});form.addEventListener('submit',e=>{e.preventDefault();finish(Object.fromEntries(new FormData(form)))});d.showModal()})}
async function rememberRoom(code, passcode) {
  const res = await nativeFetch('/api/room/membership', {
    method: 'POST',
    headers: {'X-Room-Code': code, 'X-Client-Id': client, 'Content-Type': 'application/json'},
    body: JSON.stringify({passcode}),
  });
  if (res.status === 403 || res.status === 422) throw Error('Incorrect room code or passcode.');
  if (!res.ok) throw Error('Unable to save room access. Please try again.');
  const {token} = await res.json();
  localStorage.setItem('loop-token-' + code, token);
  sessionStorage.removeItem('loop-pass-' + code);
  return token;
}

async function changeRoom(code, token) {
  roomVersion++;
  roomCode = code;
  roomToken = token;
  roomAccessRejected = false;
  machines = [];
  history.replaceState(null, '', location.pathname);
  if (!await refresh()) return false;
  localStorage.setItem('loop-token-' + code, token);
  localStorage.setItem('loop-room', code);
  updateRoomPicker();
  $('#entry-screen').hidden = true;
  $('main').hidden = false;
  $('aside').hidden = false;
  updateAlerts();
  syncAlerts();
  return true;
}
function roomFormError(detail) {
  if (typeof detail === 'string') return detail;
  const issue = detail?.[0];
  if (issue?.msg?.startsWith('Value error, ')) return issue.msg.slice('Value error, '.length);
  if (issue?.loc?.includes('name')) return 'Use a room name with 1–60 characters.';
  if (issue?.loc?.includes('passcode')) {
    return 'Use a passcode with 6–128 characters and at least one non-space character.';
  }
  return 'Check the room name and machine counts and try again.';
}

async function setupRoom(create) {
  const version = roomVersion;
  const fields = [
    {name: 'name', label: 'Room name', value: create ? '' : $('.date-card strong').textContent},
    {name: 'washers', label: 'Number of washers', type: 'number', value: create ? 3 : machines.filter(m => m.type === 'washer').length},
    {name: 'dryers', label: 'Number of dryers', type: 'number', value: create ? 3 : machines.filter(m => m.type === 'dryer').length},
  ];
  if (create) fields.push({name: 'passcode', label: 'Room passcode (6–128 characters)', type: 'password'});
  let message = '';
  try {
    while (true) {
      const result = await formDialog(create ? 'Create a laundry room' : 'Edit laundry room', fields, message);
      if (!result || (!create && version !== roomVersion)) return;
      const res = await roomRequest(create ? '/api/rooms' : '/api/room', {
        method: create ? 'POST' : 'PUT',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({...result, name: result.name.trim(), washers: Number(result.washers), dryers: Number(result.dryers)}),
      });
      const data = await res.json();
      if (res.ok) {
        if (create) await changeRoom(data.code, data.token);
        else {
          toast('Room updated');
          await refresh();
        }
        return;
      }
      message = roomFormError(data.detail);
      if (res.status !== 409 && res.status !== 422) throw Error(message);
      for (const field of fields) field.value = result[field.name];
    }
  } catch (error) {
    toast(error.message);
  }
}
$('#edit-room').onclick = () => setupRoom(false);
let accessOpen=false;
async function openRememberedRoom(code) {
  if (!code) return false;
  let token = localStorage.getItem('loop-token-' + code);
  const legacyPasscode = sessionStorage.getItem('loop-pass-' + code);
  if (!token && legacyPasscode) {
    try {
      token = await rememberRoom(code, legacyPasscode);
    } catch {
      sessionStorage.removeItem('loop-pass-' + code);
    }
  }
  if (!token) return false;
  return changeRoom(code, token);
}

async function showAccess(knownCode = '') {
  if (accessOpen) return;
  accessOpen = true;
  try {
    let code = knownCode;
    if (!code) {
      const selected = await formDialog('Join a laundry room', [{name: 'code', label: 'Room code'}]);
      if (!selected) return;
      code = selected.code.trim().toUpperCase();
      if (!code) return;
    }
    if (await openRememberedRoom(code)) return;
    if (localStorage.getItem('loop-token-' + code) && !roomAccessRejected) return;
    const login = await formDialog(`Join room ${code}`, [{name: 'passcode', label: 'Room passcode', type: 'password'}]);
    if (login) await changeRoom(code, await rememberRoom(code, login.passcode));
  } catch (error) {
    toast(error.message);
  } finally {
    accessOpen = false;
  }
}
$('#change-passcode').onclick = async () => {
  const version = roomVersion;
  const fields = [
    {name: 'current_passcode', label: 'Current passcode', type: 'password'},
    {name: 'passcode', label: 'New passcode (6–128 characters)', type: 'password'},
    {name: 'confirm_passcode', label: 'Confirm new passcode', type: 'password'},
  ];
  let message = '';
  try {
    while (version === roomVersion) {
      const result = await formDialog('Change room passcode', fields, message);
      if (!result || version !== roomVersion) return;
      if (result.passcode !== result.confirm_passcode) {
        message = 'The new passcodes do not match.';
        continue;
      }
      const res = await roomRequest('/api/room/passcode', {
        method: 'PUT',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({current_passcode: result.current_passcode, passcode: result.passcode}),
      });
      if (res.ok) {
        toast('Room passcode updated.');
        return;
      }
      message = roomFormError((await res.json()).detail);
      if (res.status !== 403 && res.status !== 422) throw Error(message);
    }
  } catch (error) {
    toast(error.message);
  }
};
$('#invite-room').onclick=()=>{const d=document.createElement('dialog');const url=new URL(location.href);url.search='?room='+encodeURIComponent(roomCode);url.hash='';d.innerHTML='<form method="dialog"><h2>Invite residents</h2><p>Share this code or link and give your housemates the passcode separately.</p><label>Room code<input readonly></label><label>Invite link<input readonly></label><button>Done</button></form>';const inputs=d.querySelectorAll('input');inputs[0].value=roomCode;inputs[1].value=url.href;document.body.append(d);d.addEventListener('close',()=>d.remove());d.showModal()};

const entry=document.createElement('section');entry.id='entry-screen';entry.className='entry-screen';entry.innerHTML=`<div class="entry-card"><div class="entry-brand">◉ loop<span>.</span></div><div class="eyebrow">LAUNDRY, IN THE LOOP</div><h1>A little more flow<br><em>to your day.</em></h1><p>Keep track of shared laundry, without creating a profile.</p><div class="entry-actions"><button id="entry-create"><strong>Create a room</strong><span>Set up a space for your building or house</span><b>↗</b></button><button id="entry-join"><strong>Join a room</strong><span>Find a room by its code</span><b>↗</b></button><button id="entry-return"><strong>Return to my room</strong><span>Open a room you joined on this browser</span><b>↗</b></button></div><p class="entry-message" role="status"></p></div>`;document.body.prepend(entry);
function returnRoomCodes() {
  const last = localStorage.getItem('loop-room');
  const codes = savedRooms().map(room => room.code);
  if (last && (codes.includes(last) || sessionStorage.getItem('loop-pass-' + last))) {
    return [last, ...codes.filter(code => code !== last)];
  }
  return codes;
}

function showEntry(message = '') {
  $('main').hidden = true;
  $('aside').hidden = true;
  entry.hidden = false;
  entry.querySelector('.entry-message').textContent = message;
  const [saved] = returnRoomCodes();
  const button = $('#entry-return');
  button.disabled = !saved;
  button.querySelector('span').textContent = saved
    ? localStorage.getItem('loop-room-name-' + saved) || `Room ${saved}`
    : 'Join a room first to return here';
}

async function returnToRoom() {
  if (accessOpen) return;
  accessOpen = true;
  const button = $('#entry-return');
  button.disabled = true;
  try {
    const codes = returnRoomCodes();
    for (const code of codes) {
      if (await openRememberedRoom(code)) return;
      if (localStorage.getItem('loop-token-' + code) && !roomAccessRejected) {
        showEntry('Couldn’t open your saved room. Try again.');
        return;
      }
    }
    showEntry(codes.length
      ? 'Your saved rooms are currently unavailable. You can try again.'
      : 'There’s no saved room access on this browser.');
  } finally {
    accessOpen = false;
    button.disabled = returnRoomCodes().length === 0;
  }
}
$('#entry-create').onclick = () => setupRoom(true);
$('#entry-join').onclick = () => showAccess(roomParam);
$('#entry-return').onclick = returnToRoom;
const startingRoom = roomParam || localStorage.getItem('loop-room');
showEntry(roomParam ? 'You have a room invitation. Choose Join a room to continue.' : '');
if (!params.has('home')) openRememberedRoom(startingRoom);
let installPrompt=null;const installButton=document.createElement('button');installButton.className='install-button';installButton.textContent='Add Loop to your home screen';document.querySelector('.entry-card').append(installButton);window.addEventListener('beforeinstallprompt',e=>{e.preventDefault();installPrompt=e});installButton.onclick=async()=>{if(installPrompt){const prompt=installPrompt;installPrompt=null;await prompt.prompt();await prompt.userChoice}else{toast('Use your browser menu and choose “Add to Home Screen” or “Install app”. On iPhone, use Share → Add to Home Screen.')}};window.addEventListener('appinstalled',()=>{installButton.hidden=true});
