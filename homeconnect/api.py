from requests_oauthlib import OAuth2Session
from .sseclient import SSEClient
from threading import Thread
import json
import os
import time


URL_API = 'https://api.home-connect.com'
URL_SIM = 'https://simulator.home-connect.com'
ENDPOINT_AUTHORIZE = '/security/oauth/authorize'
ENDPOINT_TOKEN = '/security/oauth/token'
ENDPOINT_APPLIANCES = '/api/homeappliances'

class HomeConnectError(Exception):
    pass

class HomeConnect:
    """Connection to the HomeConnect OAuth API."""
    def __init__(self, client_id, client_secret='', redirect_uri='',
                 simulate=False,
                 token_cache=None):
        """Initialize the connection."""
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.simulate = simulate
        self.oauth = None
        self.token_cache = token_cache or 'homeconnect_oauth_token.json'
        self.connect()

    def get_uri(self, endpoint):
        """Get the full URL for a specific endpoint."""
        if self.simulate:
            return URL_SIM + endpoint
        else:
            return URL_API + endpoint

    def token_dump(self, token):
        """Dump the token to a JSON file."""
        with open(self.token_cache, 'w') as f:
            json.dump(token, f)

    def token_load(self):
        """Load the token from the cache if exists it and is not expired,
        otherwise return None."""
        if not os.path.exists(self.token_cache):
            return None
        with open(self.token_cache, 'r') as f:
            token = json.load(f)
        now = int(time.time())
        token['expires_in'] = token.get('expires_at', now - 1) - now
        return token

    def token_expired(self, token):
        """Check if the token is expired."""
        now = int(time.time())
        return token['expires_at'] - now < 60

    def connect(self):
        """Connect to the OAuth APIself.

        Called at instantiation."""
        refresh_kwargs = {
            'client_id': self.client_id,
            'client_secret': self.client_secret,
        }
        refresh_url = self.get_uri(ENDPOINT_TOKEN)
        token = self.token_load()
        if token:
            self.oauth = OAuth2Session(self.client_id,
                                   token=token,
                                   auto_refresh_url=refresh_url,
                                   auto_refresh_kwargs=refresh_kwargs,
                                   token_updater=self.token_dump)
        else:
            self.oauth = OAuth2Session(self.client_id,
                                   redirect_uri=self.redirect_uri,
                                   auto_refresh_url=refresh_url,
                                   auto_refresh_kwargs=refresh_kwargs,
                                   token_updater=self.token_dump)

    def get_authurl(self):
        """Get the URL needed for the authorization code grant flow."""
        uri = self.get_uri(ENDPOINT_AUTHORIZE)
        authorization_url, state = self.oauth.authorization_url(uri)
        return authorization_url

    def get_token(self, authorization_response):
        """Get the token given the redirect URL obtained from the
        authorization."""
        uri = self.get_uri(ENDPOINT_TOKEN)
        token = self.oauth.fetch_token(uri,
            authorization_response=authorization_response,
            client_secret=self.client_secret)
        self.token_dump(token)

    def get(self, endpoint):
        """Get data as dictionary from an endpoint."""
        uri = self.get_uri(endpoint)
        res = self.oauth.get(uri)
        try:
            res = res.json()
        except:
            raise ValueError("Cannot parse {} as JSON".format(res))
        if 'error' in res:
            raise HomeConnectError(res['error'])
        elif 'data' not in res:
            raise HomeConnectError("Unexpected error")
        return res['data']

    def put(self, endpoint, data):
        """Send (PUT) data to an endpoint."""
        uri = self.get_uri(endpoint)
        res = self.oauth.put(
            uri,
            json.dumps(data),
            headers={'Content-Type': 'application/vnd.bsh.sdk.v1+json'})
        if 'error' in res:
            raise HomeConnectError(res['error'])
        return res

    def delete(self, endpoint):
        """Delete an endpoint."""
        uri = self.get_uri(endpoint)
        res = self.oauth.delete(uri)
        res = res.json()
        if 'error' in res:
            raise HomeConnectError(res['error'])
        return res

    def get_appliances(self):
        """Return a list of `HomeConnectAppliance` instances for all
        appliances."""
        data = self.get(ENDPOINT_APPLIANCES)
        return [HomeConnectAppliance(self, **app)
                for app in data['homeappliances']]


class HomeConnectAppliance:
    """Class representing a single appliance."""
    def __init__(self, hc, haId, vib=None, brand=None, type=None, name=None,
                 enumber=None, connected=False):
        self.hc = hc
        self.haId = haId
        self.vib = vib or ''
        self.brand = brand or ''
        self.type = type or ''
        self.name = name or ''
        self.enumber = enumber or ''
        self.connected = connected
        self.status = {}

    def __repr__(self):
        return "HomeConnectAppliance(hc, haId='{}', vib='{}', brand='{}', type='{}', name='{}', enumber='{}', connected={})".format(
            self.haId, self.vib, self.brand, self.type, self.name, self.enumber, self.connected)

    def listen_events(self, callback=None):
        """Spawn a thread with an event listener that updates the status."""
        uri = self.hc.get_uri('{}/{}{}'.format('/api/homeappliances', self.haId, '/events'))
        from requests.exceptions import HTTPError
        sse = None
        while True:
            try:
                sse = SSEClient(uri, session=self.hc.oauth, retry=100)
            except HTTPError:
                print('HTTPError while trying to listen')
                time.sleep(0.1)
                continue
            break
        Thread(target=self._listen, args=(sse, callback)).start()

    def _listen(self, sse, callback=None):
        """Worker function for listener."""
        for event in sse:
            try:
                self.handle_event(event, callback)
            except ValueError:
                pass

    @staticmethod
    def json2dict(lst):
        """Turn a list of dictionaries where one key is called 'key'
        into a dictionary with the value of 'key' as key."""
        return {d.pop('key'): d for d in lst}

    def handle_event(self, event, callback=None):
        """Handle a new event.

        Updates the status with the event data and executes any callback
        function."""
        event = json.loads(event.data)
        self.status.update(self.json2dict(event['items']))
        if callback is not None:
            callback(self)

    def get(self, endpoint):
        """Get data (as dictionary) from an endpoint."""
        return self.hc.get('{}/{}{}'.format(ENDPOINT_APPLIANCES, self.haId, endpoint))

    def delete(self, endpoint):
        """Delete endpoint."""
        return self.hc.delete('{}/{}{}'.format(ENDPOINT_APPLIANCES, self.haId, endpoint))

    def put(self, endpoint, data):
        """Send (PUT) data to an endpoint."""
        return self.hc.put('{}/{}{}'.format(ENDPOINT_APPLIANCES, self.haId, endpoint), data)

    def get_programs_active(self):
        """Get active programs."""
        return self.get('/programs/active')

    def get_programs_selected(self):
        """Get selected programs."""
        return self.get('/programs/selected')

    def get_programs_available(self):
        """Get available programs."""
        programs = self.get('/programs/available')
        if not programs or 'programs' not in programs:
            return []
        return [p['key'] for p in programs['programs']]

    def start_program(self, program):
        """Start a program."""
        return self.put('/programs/active', {'data': {'key': program}})

    def stop_program(self):
        """Stop a program."""
        return self.delete('/programs/active')

    def select_program(self, data):
        """Select a program."""
        return self.put('/programs/selected', data)

    def get_status(self):
        """Get the status (as dictionary) and update `self.status`."""
        status = self.get('/status')
        if not status or 'status' not in status:
            return {}
        self.status = self.json2dict(status['status'])
        return self.status

    def get_settings(self):
        """Get the current settings."""
        settings = self.get('/settings')
        if not settings or 'settings' not in settings:
            return {}
        self.status.update(self.json2dict(settings['settings']))
        return self.status

    def set_setting(self, settingkey, value):
        """Change the current setting of `settingkey`."""
        return self.put('/settings/{}'.format(settingkey), {'data': {'key': value}})
