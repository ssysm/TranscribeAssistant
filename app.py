import flask, os, uuid, subprocess, redis, config, datetime,shutil, sys
from flask import request, jsonify, logging
import spleeter.utils.logging
from multiprocessing import Process
from spleeter.separator import Separator,SpleeterError
from google.cloud import storage
from google.cloud.exceptions import GoogleCloudError

app = flask.Flask(__name__)
cache = redis.Redis(host=os.getenv('REDIS_HOST'), port=6379, db=0, decode_responses=True)
app.config["DEBUG"] = (os.getenv("FLASK_DEBUG") == "True")
app.config['UPLOAD_FOLDER'] = os.getenv("FLASK_UPLOAD_FOLDER")
spleeter.utils.logging.configure_logger(True)
storage_client = storage.Client()
bucket = storage_client.bucket(config.CONFIG['GCP_BUCKET_NAME'])
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
ALLOWED_EXTENSIONS = {'mp3','wav'}
STEMS = ['bass.wav', 'drums.wav', 'other.wav', 'piano.wav', 'vocals.wav']

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def extractAudioTracks(request_filename):
    request_id = request_filename.split('.')[0]

    upload_path = os.path.join(os.getcwd(), app.config['UPLOAD_FOLDER'], request_filename)
    output_path = os.path.join('/tmp/out/')

    cache.set(request_id, 'PROCESSING')
    separator = Separator('spleeter:5stems')
    try:
        separator.separate_to_file(upload_path, output_path)
        separator.join()
    except SpleeterError as error:
        print('Errored during separation')
        cache.set(request_id, 'ERROR;PROCESSING')
        return
    Process(target=uploadTracks, args=(request_id,output_path,upload_path,)).start()

def uploadTracks(request_id,output_path,upload_path):
    output_path = output_path + request_id
    zip_path = os.path.join('/tmp/zip/', request_id + '.zip')
    cache.set(request_id, 'ZIP')
    try:
        shutil.make_archive(request_id, 'zip', output_path)
        shutil.move(request_id + '.zip', '/tmp/zip/')
    except Exception as error:
        print(error)
        cache.set(request_id, 'ERROR;ZIP')
        return

    cache.set(request_id, 'UPLOAD_ZIP')
    try:
        blob = bucket.blob(request_id + '.zip')
        blob.upload_from_filename(zip_path)
    except GoogleCloudError as error:
        cache.set(request_id, 'ERROR;UPLOAD_ZIP')
        return

    for filename in os.listdir(output_path):
        try:
            blob = bucket.blob(request_id + '/' + filename)
            blob.upload_from_filename(output_path + '/' + filename)
            cache.set(request_id, 'UPLOAD_IND')
        except Exception as error:
            cache.set(request_id, 'ERROR;UPLOAD_IND')
            return
    cleanWorkArea(output_path, zip_path,upload_path, request_id)
    cache.set(request_id, 'DONE')
    cache.expire(request_id, 3600)

def cleanWorkArea(output_path, zip_path, upload_path, request_id):
    shutil.rmtree(output_path)
    os.unlink(zip_path)
    os.unlink(upload_path)

@app.route("/", methods=['GET'])
def getRoot():
    return "Transcribe Assistant"

@app.route("/process_status", methods=['GET'])
def checkProcessStatus():
    request_id = request.args.get('requestID')
    file_status = cache.get(request_id)
    if request_id == '' or file_status is None:
        return jsonify({
            'success': 'false',
            'data': {
                'msg': 'No process ID was provided or does not exist.'
            }
        })
    return jsonify({
            'success': 'true',
            'data': {
                'status': file_status
            }
        })

@app.route("/retrive_tracks", methods=['GET'])
def generateDownloadLinks():
    request_id = request.args.get('requestID')
    file_status = cache.get(request_id)
    if request_id == '' or file_status is None:
        return jsonify({
            'success': 'false',
            'data': {
                'msg': 'No process ID was provided or does not exist.'
            }
        })
    if file_status != 'DONE':
        return jsonify({
            'success': 'false',
            'data': {
                'msg': 'Tracks have not been generated.'
            }
        })
    file_ttl = cache.ttl(request_id)
    file_url_dict = {'zip': generate_gcp_url(request_id + '.zip')}
    for i in range(len(STEMS)):
        file_url_dict[STEMS[i]] = generate_gcp_url(request_id + '/' + STEMS[i])
    return jsonify({
        'success': 'true',
        'data': {
            'urls': file_url_dict,
            'ttl': file_ttl
        }
    })

def generate_gcp_url(key):
    blob = bucket.blob(key)

    url = blob.generate_signed_url(
        version="v4",
        # This URL is valid for 60 minutes
        expiration=datetime.timedelta(minutes=60),
        # Allow GET requests using this URL.
        method="GET",
    )

    return url

@app.route("/request_audio_sep", methods=['POST'])
def submitAudio():
    if 'file' not in request.files:
        return jsonify({
            'success': 'false',
            'data': {
                'msg': 'No file was uploaded.'
            }
        })
    file = request.files['file']
    if file.filename == '':
        return jsonify({
            'success': 'false',
            'data': {
                'msg': 'No file selected.'
            }
        })
    if file and allowed_file(file.filename):
        request_id = str(uuid.uuid4())
        file_split = file.filename.split('.')
        file_ext = file_split[len(file_split) - 1]
        filename = request_id + '.' + file_ext
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        cache.set(request_id, 'SUBMITTED')
        Process(target=extractAudioTracks, args=(filename,)).start()
        return jsonify({
            'success': 'true',
            'data': {
                'requestID': request_id
            }
        })

if __name__ == '__main__':
    app.run()