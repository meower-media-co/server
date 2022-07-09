from datetime import datetime
import time
import string
import requests
from threading import Thread
import os
import json
import serial
from cryptography.fernet import Fernet
from uuid import uuid4
import secrets

class Utils:
    def __init__(self, meower, request):
        self.meower = meower
        self.request = request

        # Create ratelimits
        meower.ratelimits = {}

        # Create permitted lists of characters for posts
        self.permitted_chars_post = []
        self.permitted_chars_post.extend(string.ascii_letters)
        self.permitted_chars_post.extend(string.digits)
        self.permitted_chars_post.extend(string.punctuation)
        self.permitted_chars_post.append(" ")

        # Create permitted lists of characters for usernames
        self.permitted_chars_username = self.permitted_chars_post.copy()
        for item in [
            '"',
            "'",
            "*",
            ";"
        ]:
            self.permitted_chars_username.remove(item)

    def log(self, msg, prefix=None):
        if prefix is None:
            print("{0}: {1}".format(self.timestamp(4), msg))
        else:
            print("[{0}] {1}: {2}".format(prefix, self.timestamp(4), msg))

    def timestamp(self, ttype, epoch=int(time.time())):
        today = datetime.fromtimestamp(epoch)
        if ttype == 1:
            return dict({
                "mo": str(datetime.now().strftime("%m")),
                "d": str(datetime.now().strftime("%d")),
                "y": str(datetime.now().strftime("%Y")),
                "h": str(datetime.now().strftime("%H")),
                "mi": str(datetime.now().strftime("%M")),
                "s": str(datetime.now().strftime("%S")),
                "e": int(time.time())
            })
        elif ttype == 2:
            return str(today.strftime("%H%M%S"))
        elif ttype == 3:
            return str(today.strftime("%d%m%Y%H%M%S"))
        elif ttype == 4:
            return str(today.strftime("%m/%d/%Y %H:%M.%S"))
        elif ttype == 5:    
            return str(today.strftime("%d%m%Y"))

    def create_session(self, type, user, token, email=None, expires=None, action=None, app=None, scopes=None):
        # Base session data
        session_data = {
            "_id": str(uuid4()),
            "type": type,
            "user": user,
            "user_agent": (self.request.headers.get("User-Agent") if "User-Agent" in self.request.headers else None),
            "token": token,
            "expires": None,
            "created": time.time()
        }
        
        # Add specific data for each type
        if type == 0:
            session_data["action"] = action
            session_data["email"] = email
        elif type == 1:
            session_data["verified"] = False
        elif type == 4:
            session_data["app"] = app
            session_data["scopes"] = scopes
        elif type == 5:
            session_data["app"] = app
            session_data["scopes"] = scopes
            session_data["refresh_token"] = str(secrets.token_urlsafe(128))
            session_data["refresh_expires"] = time.time() + 31556952
            session_data["previous_refresh_tokens"] = []

        # Add any missing data
        for item in ["email", "action", "verified", "app", "scopes", "refresh_token", "refresh_expires", "previous_refresh_tokens"]:
            if item not in session_data:
                session_data[item] = None

        # Set expiration time
        if expires is not None:
            session_data["expires"] = time.time() + expires
        else:
            session_data["expires"] = time.time() + {1: 300, 2: 300, 3: 31556952, 4: 1800}[session_data["type"]]

        # Add session to database and return session data
        self.meower.db["sessions"].insert_one(session_data)
        return session_data

    def foundation_session(self, user):
        # Create session
        session = self.create_session(3, user, secrets.token_urlsafe(64))
        del session["previous_refresh_tokens"]

        # Get user data and check if it's pending deletion
        userdata = self.meower.db["usersv0"].find_one({"_id": user})
        if userdata["security"]["delete_after"] is not None:
            self.meower.db["usersv0"].update_one({"_id": userdata["_id"]}, {"$set": {"security.delete_after": None}})
        del userdata["security"] # Delete security before returning to user

        # Return session data
        return {"session": session, "user": userdata, "requires_totp": False}

    def check_for_spam(self, type, client, seconds=1):
        if not (type in self.meower.ratelimits):
            self.meower.ratelimits[type] = {}

        if not (client in self.meower.ratelimits[type]):
            self.meower.ratelimits[type][client] = 0
        
        if self.meower.ratelimits[type][client] > time.time():
            return True
        else:
            self.meower.ratelimits[type][client] = time.time() + seconds
            return False

    def check_for_bad_chars_username(self, message):
        for char in message:
            if not char in self.permitted_chars_username:
                return True
        return False

    def check_for_bad_chars_post(self, message):
        for char in message:
            if not char in self.permitted_chars_post:
                return True
        return False

    def user_status(self, user):
        userdata = self.meower.db["usersv0"].find_one({"_id": user})
        if userdata is None:
            return "Offline"
        else:
            status = userdata["profile"]["status"]
            if userdata["security"]["banned"]:
                return "Banned"
            elif userdata["security"]["suspended_until"] > time.time():
                return "Suspended"
            else:
                if user in self.meower.sock_clients:
                    if (status < 0) or (status > 3):
                        return "Online"
                    else:
                        return ["Offline", "Online", "Away", "Do Not Disturb"][status]
                else:
                    return "Offline"

    def send_payload(self, payload, user=None):
        if user is None:
            for user, clients in self.meower.sock_clients.items():
                for sock_client in clients:
                    sock_client.client.send(payload)
        else:
            if user in self.meower.sock_clients:
                for sock_client in self.meower.sock_clients[user]:
                    sock_client.client.send(payload)

    def init_encryption(self):
        self.meower.meowkey = None
        self.meower.encryption = None
        try:
            if os.environ["ENCRYPTION_KEY_FROM"] == "env":
                key = os.environ["ENCRYPTION_KEY"]
            elif os.environ["ENCRYPTION_KEY_FROM"] == "meowkey":
                self.meower.meowkey = EasyUART(os.environ["MEOWKEY_PORT"])
                self.meower.meowkey.connect()
                self.meower.meowkey.rx()
                self.meower.meowkey.tx(json.dumps({"cmd": "ACK?"}))
                key = self.meower.meowkey.rx()
            if key is not None:
                self.meower.encryption = Fernet(key.encode())
        except:
            pass
        if "encryption_keys" not in os.listdir():
            os.mkdir("encryption_keys")
        if self.meower.encryption is None:
            self.log("Failed to initialize encryption -- Emails will not work")

    def destroy_key_on_meowkey_disconnect(self):
        while not self.meower.meowkey.bus.connected:
            self.meower.meowkey = None
            self.meower.encryption = None
            self.meower.log("Disconnected from MeowKey -- Emails will no longer work")

    def encrypt(self, data):
        new_key = Fernet.generate_key()
        encrypted_data = Fernet(new_key).encrypt(data.encode()).decode()
        new_uuid = str(uuid4())
        with open("encryption_keys/{0}".format(new_uuid), "w") as f:
            f.write(self.meower.encryption.encrypt(new_key).decode())
        return new_uuid, encrypted_data

    def decrypt(self, id, data):
        with open("encryption_keys/{0}".format(id), "r") as f:
            encryption_key = self.meower.encryption.decrypt(f.read().encode()).decode()
        decrypted_data = Fernet(encryption_key).decrypt(data.encode()).decode()
        return decrypted_data

    def is_valid_email(self, email):
        # Check if the email contains an @
        if "@" not in email:
            return False
        
        # Check if the email address domain is valid
        email = email.split("@")
        if email[1].count(".") != 1:
            return False
        else:
            return True

    def send_email(self, email, username, subject, body, type="text/plain"):
        payload = {
            "personalizations": [{
                "to": [{
                    "email": email,
                    "name": username
                }],
                "dkim_domain": os.getenv("EMAIL_DOMAIN"),
                "dkim_selector": "mailchannels",
                "dkim_private_key": os.getenv("EMAIL_DKIM_KEY")
            }],
            "from": {
                "email": "no-reply@{0}".format(os.getenv("EMAIL_DOMAIN")),
                "name": "Meower"
            },
            "subject": subject,
            "content": [{
                "type": type,
                "value": body
            }]
        }

        return requests.post(os.getenv("EMAIL_WORKER_URL"), headers={"X-Auth-Token": os.getenv("EMAIL_WORKER_TOKEN")}, json=payload)

    def init_db(self):
        with open("db_template.json", "r") as f:
            db_data = json.loads(f.read())
        for collection_name, collection_data in db_data.items():
            for index_name in collection_data["indexes"]:
                try:
                    self.meower.db[collection_name].create_index(index_name)
                except:
                    pass
            for item in collection_data["items"]:
                try:
                    self.meower.db[collection_name].insert_one(item)
                except:
                    pass

    def check_for_json(self, data=[]):
        for item in data:
            if item not in self.request.json:
                return self.meower.respond({"type": "missingField", "message": "Missing required data: {0}".format(item)}, 400)
    
    def require_auth(self, allowed_types, levels=[-1, 0, 1, 2, 3], scope=None, check_suspension=False):
        if self.request.method != "OPTIONS":
            # Check if session is valid
            if not self.request.session.authed:
                return self.meower.respond({"type": "unauthorized", "message": "You are not authenticated."}, 401)
            
            # Check session type
            if self.request.session.type not in allowed_types:
                return self.meower.respond({"type": "forbidden", "message": "You are not allowed to perform this action."}, 403)
            
            # Check session scopes
            if (self.request.session.type == 5) and (scope not in self.request.session.scopes):
                return self.meower.respond({"type": "forbidden", "message": "You are not allowed to perform this action."}, 403)

            # Check if session is verified (only for certain types)
            if (self.request.session.verified != None) and (self.request.session.verified != True):
                return self.meower.respond({"type": "unauthorized", "message": "Session has not been verified yet."}, 401)

            # Check user
            userdata = self.meower.db["usersv0"].find_one({"_id": self.request.session.user})
            if (userdata is None) or userdata["deleted"] or userdata["security"]["banned"]:
                self.request.session.delete()
                return self.meower.respond({"type": "unauthorized", "message": "You are not authenticated."}, 401)
            elif userdata["state"] not in levels:
                return self.meower.respond({"type": "forbidden", "message": "You are not allowed to perform this action."}, 403)
            elif check_suspension and (userdata["suspended_until"] > time.time()):
                return self.meower.respond({"type": "forbidden", "message": "You are suspended from performing this action."}, 403)

class Session:
    def __init__(self, meower, token):
        self.meower = meower

        # Get session data from database
        token_data = self.meower.db.sessions.find_one({"token": token})
        
        # Check if session is valid
        self.authed = False
        try:
            if (token_data is not None) and (token_data["type"] == 3 or token_data["type"] == 5):
                self.json = token_data
                for key, value in token_data.items():
                    setattr(self, key, value)
                if (not (self.expires < time.time())) or (self.expires == None):
                    self.authed = True
        except:
            pass

    def renew(self):
        # Renew session
        self.meower.db.sessions.update_one({"_id": self._id}, {"$set": {"expires": time.time() + self.expires}})
        self.expires = time.time() + self.expires
    
    def delete(self):
        # Delete session
        self.meower.db.sessions.delete_one({"_id": self._id})

class EasyUART:
    def __init__(self, port):
        self.bus = serial.Serial(port = port, baudrate = 9600)
        
    def connect(self): # This code is platform specific
        if not self.bus.connected:
            while not self.bus.connected:
                time.sleep(1)
        self.bus.reset_input_buffer()
        return True
    
    def tx(self, payload): # Leave encoding as ASCII since literally everything supports it
        self.bus.write(bytes(payload + "\r", "ASCII"))
    
    def rx(self):
        done = False
        tmp = ""
        while not done:
            # Listen for new data
            if self.bus.in_waiting != 0:
                readin = self.bus.read(self.bus.in_waiting).decode("ASCII")
                
                for thing in readin:
                    if thing == "\r":
                        done = True
                    else:
                        tmp += thing
        return tmp