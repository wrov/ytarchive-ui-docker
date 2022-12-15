import os
import glob
import falcon
import json
import sys

import multiprocessing
import subprocess
import shlex
from multiprocessing.pool import ThreadPool

import urllib.request
import shutil

import traceback

if os.path.isfile("./callbacks.py"):
    from callbacks import callbacks
else:
    callbacks = None

pool = ThreadPool(processes=int(os.getenv('PROCESSES', multiprocessing.cpu_count())))

# ----- ytarchive upgrade -----
def get_latest_ytarchive_commit():
    url = "https://api.github.com/repos/Kethsar/ytarchive/commits"
    req = urllib.request.Request(url)
    
    response = urllib.request.urlopen(req)
    encoding = response.info().get_content_charset('utf-8')
    resp_data = json.loads(response.read().decode(encoding))

    commit = resp_data[0]["sha"]

    return commit

def get_latest_ytarchive():
    url = "https://raw.githubusercontent.com/Kethsar/ytarchive/master/ytarchive.py"

    with urllib.request.urlopen(url) as response, open("./ytarchive.py", 'wb') as out_file:
        shutil.copyfileobj(response, out_file)

    commit = get_latest_ytarchive_commit()

    with open("./ytarchive.commit", 'w') as f:
        f.write(commit)

if not os.path.isfile("./ytarchive.commit"):
    if os.path.isfile("./ytarchive.py"):
        print("[INFO] Unknown ytarchive version. Redownloading...")
        os.remove("./ytarchive.py")
    else:
        print("[INFO] ytarchive not found. Downloading...")
    get_latest_ytarchive()

else:
    if not os.path.isfile("./ytarchive.py"):
        print("[INFO] ytarchive not found. Downloading...")
        get_latest_ytarchive()
    else:
        with open("./ytarchive.commit", 'r') as f:
            print("[INFO] Checking if ytarchive is latest...")
            commit = f.read()
            if commit != get_latest_ytarchive_commit():
                print("[INFO] Upgrading ytarchive...")
                get_latest_ytarchive()
                print("[INFO] Finished.")
            else:
                print("[INFO] Using latest ytarchive!")

# ----- ytarchive upgrade END -----
def archive(url, quality, params={}, callback_ids=[], on_callback=None, on_main_finished=None):
    cmd = f"'{sys.executable}' ./ytarchive.py"
    for k, v in params.items():
        if type(v) == bool:
            cmd += f" {k}"
        else:
            cmd += f" {k} '{v}'"
    cmd += f" {url} {quality}"
    p = subprocess.Popen(shlex.split(cmd), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = p.communicate()
    
    if type(out) == bytes:
        out = out.decode(sys.stdout.encoding)
    if type(err) == bytes:
        err = err.decode(sys.stdout.encoding)

    if on_main_finished:
        on_main_finished(url, quality, params, callback_ids, on_callback)

    if callbacks and callback_ids:
        filepath = out.split("Final file: ")[-1].rstrip()
        filepath = os.path.abspath(filepath)

        for callback_id_index in range(len(callback_ids)):
            callback_id = callback_ids[callback_id_index]
            if len(err):
                err += f"\n\n [INFO] Queued callback id: {callback_id}"
                yield (out, err, True)
                err = ''

            if on_callback:
                on_callback(callback_id_index)
            
            tmp = callbacks[callback_id](filepath)

            if "front" in tmp and tmp["front"]:
                for key in tmp["front"]:
                    _out = tmp["front"][key]["out"]
                    _err = tmp["front"][key]["err"]

                    out = f"{key}:\n{_out}\n\n{out}" 
                    if len(tmp["front"][key]["err"]):
                        err = f"{key}:\n{_err}\n\n{err}" 
            
            if "end" in tmp and tmp["end"]:
                for key in tmp["end"]:
                    _out = tmp["end"][key]["out"]
                    _err = tmp["end"][key]["err"]

                    out += f"\n\n{key}:\n{_out}" 
                    if len(tmp["end"][key]["err"]):
                        err += f"\n\n{key}:\n{_err}" 
        
    yield (out, err, False)

statuses = {}

def get_id(x):
    if x not in statuses:
        return x

    i = 0
    while True:
        tmp = f"{x}.{i}"
        if tmp not in statuses:
            return tmp
        i += 1

def add_task(uid, task, callback=False):
    global statuses
    if uid in statuses:
        statuses[uid]["task"] = task
    else:
        if not callback:
            statuses[uid] = {"task": task}
        else:
            statuses[uid] = {
                "task": task, 
                "callbacks": {
                    "queue": [],
                    "current": -1
                }
            }

class Status:
    def on_get(self, req, resp):
        global statuses

        resp.media = {}

        for uid in statuses:
            t = statuses[uid]["task"]
            if t.ready():
                try:
                    out, err, is_unfinished = t.get()
                    resp.media[uid] = {
                        "status": 1 if not len(err) else 2,
                        "output": {"out": out, "err": err},
                        "isUnfinished": is_unfinished
                    }
                except Exception as err:
                    resp.media[uid] = {
                        "status": 2,
                        "output": {"out": None, "err": traceback.format_exc()},
                        "isUnfinished": False
                    }
            elif ("callbacks" in statuses[uid]) and statuses[uid]["callbacks"]["current"] != -1:
                resp.media[uid] = {
                    "status": 3,
                    "callbacks": statuses[uid]["callbacks"]
                }
            else:
                resp.media[uid] = False

        resp.status = falcon.HTTP_200
    def on_delete(self, req, resp):
        global statuses

        uid = req.media.get('id')
        statuses.pop(uid, None)

        resp.status = falcon.HTTP_200

class Record:
    def on_post(self, req, resp):
        global pool

        youtube_id = req.media.get('youtubeID')
        url = f"https://youtu.be/{youtube_id}"
        quality = req.media.get('quality')
        params = req.media.get('params')

        uid = get_id(youtube_id)

        callback_ids = req.media.get('callbacks') if callbacks else []

        if callback_ids:
            def on_callback(callback_index):
                statuses[uid]["callbacks"]["current"] = callback_index
            def on_main_finished(url, quality, params, callback_ids, on_callback):
                statuses[uid]["callbacks"]["queue"] = callback_ids
        else:
            on_callback = None
            on_main_finished = None
        
        archive_gen = archive(url, quality, params, callback_ids, on_callback, on_main_finished)
        t = pool.apply_async(lambda: next(archive_gen))
        add_task(uid, t, callback=True)
        statuses[uid]["generator"] = archive_gen

        resp.media = {'id': uid}
        resp.status = falcon.HTTP_200

class Website:
    def on_get(self, req, resp):
        resp.status = falcon.HTTP_200
        resp.content_type = "text/html"
        with open("./index.html", "rb") as f:
            resp.body = f.read()

class CookieAvailable:
    def on_get(self, req, resp):
        if os.path.isfile("./cookie.txt"):
            resp.status = falcon.HTTP_302
        else:
            resp.status = falcon.HTTP_404

class Reboot:
    def on_get(self, req, resp):
        resp.status = falcon.HTTP_200
        sys.exit(0)

class Callbacks:
    def on_get(self, req, resp):
        if callbacks:
            resp.media = [x for x in callbacks]
            resp.status = falcon.HTTP_200
        else:
            resp.status = falcon.HTTP_404

class Callback:
    def on_get(self, req, resp):
        uid = req.get_param('id')
        t = pool.apply_async(lambda: next(statuses[uid]["generator"]))
        add_task(uid, t)

        resp.status = falcon.HTTP_200

class Download:
    def on_get(self, req, resp):
        uid = req.get_param('id')
        for file in glob.glob('*' + uid + '*'):
            resp.content_type = 'application/octet-stream'
            resp.downloadable_as = file
            resp.text = file
            return
        
api = falcon.API()
api.add_route('/status', Status())
api.add_route('/record', Record())
api.add_route('/cookie', CookieAvailable())
api.add_route('/callbacks', Callbacks())
api.add_route('/callback', Callback())
api.add_route('/reboot', Reboot())
api.add_route('/download', Download())
api.add_route('/', Website())
