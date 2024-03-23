import os, sys
import hashlib
import io
import json
import requests
from datetime import datetime
py_version = sys.version_info.major
if py_version == 3:
    from subprocess import PIPE, run
elif py_version == 2:
    from subprocess import PIPE, Popen
import shlex
import re
import platform



# $$$$$$$\  $$$$$$$$\ $$\    $$\  $$$$$$\  $$$$$$$\   $$$$$$\  
# $$  __$$\ $$  _____|$$ |   $$ |$$  __$$\ $$  __$$\ $$  __$$\ 
# $$ |  $$ |$$ |      $$ |   $$ |$$ /  $$ |$$ |  $$ |$$ /  \__|
# $$ |  $$ |$$$$$\    \$$\  $$  |$$ |  $$ |$$$$$$$  |\$$$$$$\  
# $$ |  $$ |$$  __|    \$$\$$  / $$ |  $$ |$$  ____/  \____$$\ 
# $$ |  $$ |$$ |        \$$$  /  $$ |  $$ |$$ |      $$\   $$ |
# $$$$$$$  |$$$$$$$$\    \$  /    $$$$$$  |$$ |      \$$$$$$  |
# \_______/ \________|    \_/     \______/ \__|       \______/ 
                                                             

# __/\\\\\\\\\\\\\____/\\\________/\\\____/\\\\\\\\\_________/\\\\\\\\\\\\__/\\\\\\\\\\\\\\\_        
#  _\/\\\/////////\\\_\/\\\_______\/\\\__/\\\///////\\\_____/\\\//////////__\/\\\///////////__       
#   _\/\\\_______\/\\\_\/\\\_______\/\\\_\/\\\_____\/\\\____/\\\_____________\/\\\_____________      
#    _\/\\\\\\\\\\\\\/__\/\\\_______\/\\\_\/\\\\\\\\\\\/____\/\\\____/\\\\\\\_\/\\\\\\\\\\\_____     
#     _\/\\\/////////____\/\\\_______\/\\\_\/\\\//////\\\____\/\\\___\/////\\\_\/\\\///////______    
#      _\/\\\_____________\/\\\_______\/\\\_\/\\\____\//\\\___\/\\\_______\/\\\_\/\\\_____________   
#       _\/\\\_____________\//\\\______/\\\__\/\\\_____\//\\\__\/\\\_______\/\\\_\/\\\_____________  
#        _\/\\\______________\///\\\\\\\\\/___\/\\\______\//\\\_\//\\\\\\\\\\\\/__\/\\\\\\\\\\\\\\\_ 
#         _\///_________________\/////////_____\///________\///___\////////////____\///////////////__


__APP_NAME = "purge"
__VERSION = "1.0.0"
__GITHUB = "https://github.com/anouarharrou/log-purger/purge.py"
__AUTO_UPDATE = True
__PURGE_CONFIG_FILE = "./purge_config.json"
__DEFAULT_PATH = "/home/devops"
__PURGE_PATH = "{}/purge".format(__DEFAULT_PATH)
__PURGE_PATH_LOG = "{}/logs".format(__PURGE_PATH)
__PURGE_PATH_LOG_NAME = "{}/logs/purge.log".format(__PURGE_PATH)
__PURGE_PATH_DATA = "{}/data".format(__PURGE_PATH)
__PURGE_ROTATE = True
__PURGE_LOG = True
__PURGE_FD = None
__HOSTNAME = platform.node()
SUCCESS = True
FAIL = False
now = datetime.now()
backup_date = now.strftime("%d%m%Y_%H%M")

file_name = ""
file_size = ""
md5 = ""
compressed_file_name = ""
compressed_file_size = ""
compressed_md5 = ""
original_path = ""


print("\033[92m")  # ANSI escape code for green color
print("🟢🤖 I Live in your Server now 🤖🟢")
print("\033[90m")  # ANSI escape code for grey color for the rest 


def update_purge():
    if not __AUTO_UPDATE:
        return False
    __latest_version = "0.0.0"
    try:
        response = requests.get(__GITHUB, verify=False)
    except:
        print_log("Cant reach: {}  [Skip]".format(__GITHUB), print_only = True)
        return False
    print_log("Current version: {}".format(__VERSION), print_only = True)
    if response.status_code == 200:
        print_log("Fetch {} [ OK ]".format(__GITHUB), print_only = True)
        for ln in response.text.split("\n"):
            reg = re.search(r"^__VERSION.+", ln)
            if reg:
                __latest_version = ln
                break
        try:
            __latest_version = re.sub('=|"', "", str(__latest_version)).split()[-1]
        except:
            __latest_version = "0.0.0"
        if __latest_version != __VERSION:
            print_log("save {} version {} this new version will be executed next time".format(__APP_NAME, __latest_version), print_only = True)
            with open('./purge.py', 'wb') as purge:
                purge.write(response.content)
        else:
            print_log("{} already uptodate".format(__APP_NAME), print_only = True)
    else:
        print_log("Fetch {} [ FAIL ]".format(__GITHUB), print_only = True)


def print_log(str_print = "", log_date = True, print_only = False):
    dt_now = datetime.now()
    date_time = dt_now.strftime("%m/%d/%Y - %H:%M:%S")
    if not log_date:
        date_time = ' ' * len(date_time)
    if __PURGE_LOG and not print_only:
        __PURGE_FD.write("{} - {}\n".format(date_time, str_print))
    print("{} - {}".format(date_time, str_print))

def __create_dir(path_name):
    if not os.path.isdir(path_name) and not os.path.exists(path_name):
        try:
            if py_version == 3:
                os.makedirs(path_name, exist_ok = True)
            elif py_version == 2:
                os.makedirs(path_name)
        except OSError as error:
            print_log("Cannot create directory {} - {}".format(path_name, error), print_only = True)
            print_log("Stop the execution.", print_only = True)
            sys.exit(-1)
        except:
            print_log("Cannot create directory {} - UNKOWN ERROR".format(path_name), print_only = True)
            print_log("Stop the execution.", print_only = True)
            sys.exit(-2)
        else:
            print_log("Create directory {} [ CREATED ]".format(path_name), print_only = True)
    else:
        print_log("Directory {} - [ Exist ]".format(path_name), print_only = True)

def __rotate_purge_log(purge_log):
    __dt = datetime.now()
    __dtf = __dt.strftime("%d%m%Y_%H%M")
    if os.path.exists(purge_log) and os.path.isfile(purge_log):
        print_log("An old logfile found, [ ROTATE ]", print_only = True)
        try:
            os.system("mv {} {}_{}".format(purge_log, purge_log, __dtf))
        except OSError as error:
            print_log("ERROR occured, during logrotation", print_only = True)
            sys.exit(-3)

def __init_create_directory_structure():
    __create_dir(__PURGE_PATH)
    __create_dir(__PURGE_PATH_LOG)
    __create_dir(__PURGE_PATH_DATA)
    # __create_dir(__TOOLS_APTH)
    __rotate_purge_log(__PURGE_PATH_LOG_NAME)

def __load_config_file(__config_file):
    __data = ""
    if not os.path.exists(__config_file):
        print_log('The configuration file doesnt exist.', print_only = True)
        sys.exit(-5)
    with open(__config_file, "r") as file:
        __data = json.load(file)
    return __data


data = __load_config_file(__PURGE_CONFIG_FILE)
config = data["config"]
services = data["services"]
BUCKET = config["bucket"]
KEY = config["key"]
SECRET = config["secret"]
SERVER = config["server"]
PROJECT = config["project"]

update_purge()
__init_create_directory_structure()


if __PURGE_LOG == True:
    __PURGE_FD = open(__PURGE_PATH_LOG_NAME, 'a')

def upload_cmd(command):
    try:
        if py_version == 3:
            result = run(command, stdout=PIPE, stderr=PIPE, universal_newlines=True, shell=True)
            output = result.stdout.splitlines()
        elif py_version == 2:
            prc = Popen(command, stdout=PIPE, stderr=PIPE, shell=True)
            result = prc.stdout.readlines()
            output = map(lambda s: s.strip(), result)
        
        # Check if there is any output
        if len(output) > 0:
            end_of_execution = output[-1]
            only_one_file_to_upload = output[0].split()[-1]
            
            # Check if the command was successful and at least one file was uploaded
            if end_of_execution == "Done" and int(only_one_file_to_upload) > 0:
                return SUCCESS
            else:
                print_log("Failed to upload files. Command output: {}".format(output))
                return FAIL
        else:
            print_log("No output received from the command")
            return FAIL
    except Exception as e:
        print_log("An error occurred during command execution: {}".format(e))
        return FAIL

def __load_files(__path):
    __t_files = []
    listed_files = os.listdir(log_path)
    for fn in listed_files:
        _t = re.findall(pattern, fn)
        if len(_t) == 1:
            __t_files.append(_t[0])
    return __t_files

def __md5sum(src, length=io.DEFAULT_BUFFER_SIZE):
    md5 = hashlib.md5()
    with io.open(src, mode="rb") as fd:
        for chunk in iter(lambda: fd.read(length), b''):
            md5.update(chunk)
    return md5

def file_stat(__file_name):
    __file_stat = {
        "file_path": "",
        "file_name": "",
        "bytes_size": 0,
        "mb_size": 0,
        "uid": None,
        "gid": None,
        "md5": None
    }
    try:
       __fs = os.stat(__file_name)
       __file_stat["file_path"], __file_stat["file_name"] = os.path.split(__file_name)
       __file_stat["bytes_size"] = __fs.st_size
       __file_stat["mb_size"] = float("{:.2f}".format(__fs.st_size * 0.000001))
       __file_stat["uid"] = __fs.st_uid
       __file_stat["gid"] = __fs.st_gid
       __file_stat["md5"] = __md5sum(__file_name).hexdigest()
    except:
       __file_stat["file_path"] = "unkown"
       __file_stat["file_name"] = "unkown"
       __file_stat["bytes_size"] = "unkown"
       __file_stat["mb_size"] = 0
       __file_stat["uid"] = "unkown"
       __file_stat["gid"] = "unkown"
       __file_stat["md5"] = 0
    return __file_stat


for service in services:
    service_name = service['service']
    try:
        log_path = service['log_path']
    except:
        log_path = "/home/devops/logs"

    try:
        pattern = service['pattern']
    except:
        pattern = "^service-.+"

    try:
        compress = service['compress']
    except:
        compress = True

    try:
        #remove_on_transfer = service["RemoveOnTransfer"]
        RemoveOnTransfert = service['RemoveOnTransfert']
    except:
        RemoveOnTransfert = True
    ####tracked_files_pattern = f"{log_path}/{pattern}"

    print_log("service name : {}".format(service_name))
    print_log("log_path = {} - pattern = {}".format(log_path, pattern), log_date=False)
    print_log("compress = {}".format(compress), log_date=False)
    print_log("RemoveOnTransfert = {}".format(RemoveOnTransfert), log_date=False)
    print_log("RemoveOnTransfert = {}".format(backup_date), log_date=False)

    target_files = __load_files(log_path)
    files_number = len(target_files)
    print_log("{} will be processed.".format(files_number), log_date=False)
    print_log("The follwing files will be uploaded to the s3: {}".format(target_files))

    for target in target_files:
        __upload_path = "/{}/{}/{}/{}".format(PROJECT, service_name, backup_date, __HOSTNAME)
        print_log("** processing : {}".format(file_stat("{}/{}".format(log_path, target))), log_date=False)
        if compress:
            result = None
            try:
                if not target.endswith(".gz"):
                    result = os.system("gzip -f -q -9 {}/{}".format(log_path, target))
                else:
                    result = 2
            except:
                result = -1
            if result == 0:
                compression = SUCCESS
                print_log("✅ {} file compressed with SUCCESS.".format(target))  # Emoji for success and green color
            elif result == 2:
                compression = SUCCESS
                print_log("🟢 {} file already compressed.".format(target))  # Emoji for already compressed and green color
            else:
                compression = FAIL
                print_log("❌ {} file compression FAILED.".format(target))  # Emoji for failure and red color

        if compress and compression:
            # transfert the file to s3
            if not target.endswith(".gz"):
                target = "{}.gz".format(target)
            print_log("** compressing : {}".format(file_stat("{}/{}".format(log_path, target))), log_date=False)


        if compress and compression == FAIL:
            # skeep the transfert and keep the file in the server
            upload_to_s3_cmd = False
        else:
            upload_to_s3_cmd = 'AWS_ACCESS_KEY_ID={} AWS_SECRET_ACCESS_KEY={} aws s3 cp {} s3://{}/{}/{}/{}/{}/'.format(
                KEY,
                SECRET,
                os.path.join(log_path, target),
                BUCKET,
                PROJECT,
                service_name,
                backup_date,
                __HOSTNAME
            )
            upload_to_s3_bucket = SUCCESS    

        if upload_to_s3_cmd != False:
            upload_status = upload_cmd(upload_to_s3_cmd)
            if upload_to_s3_bucket == SUCCESS:
                print_log("📤 {}/{} uploaded to S3 bucket: {} in path: {}  with [ SUCCESS ]".format(
                    log_path,
                    target,
                    BUCKET,
                    __upload_path
                ))  # Emoji for success and blue color
                if RemoveOnTransfert:
                    try:
                        rm_result = os.system("rm -f {}/{}".format(log_path, target))
                        if rm_result == 0:
                            print_log("🗑️ RemoveOnTransfert = {} [Removing the original file from the server: {}/{}]".format(RemoveOnTransfert, log_path, target))  # Emoji for trash bin and green color
                    except:
                        print_log("❌ RemoveOnTransfert = {} [ERROR WHEN EXECUTING RM COMMAND]".format(RemoveOnTransfert))  # Emoji for failure and red color
            else:
                print_log("❌ {}/{} uploaded to S3 bucket: {} in path: {}  [ FAILED ]".format(
                    log_path,
                    target,
                    BUCKET,
                    __upload_path
                ))  # Emoji for failure and red color
                if RemoveOnTransfert:
                    print_log("RemoveOnTransfert = {} [The upload was failed so we keep the original file in the server]".format(RemoveOnTransfert))
                print_log("-")

if __PURGE_LOG == True:
    __PURGE_FD.close()
