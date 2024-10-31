import datetime
from flask import jsonify, send_file, Response
from app.utils import DatabaseHandler
from app.utils import CacheHandler
from bson import json_util
import json
from bson.objectid import ObjectId
from app.api.records.models import Record as FileRecord
from app.utils.LogActions import log_actions
from app.api.logs.services import register_log
from app.api.users.services import has_right
from app.api.records.models import RecordUpdate as FileRecordUpdate
from app.utils.functions import cache_get_record_stream, cache_get_record_transcription, cache_get_record_document_detail, cache_get_pages_by_id, cache_get_block_by_page_id, cache_get_imgs_gallery_by_id, cache_get_processing_metadata
from werkzeug.utils import secure_filename
import os
import shutil
import hashlib
import magic
import uuid
from dotenv import load_dotenv
load_dotenv()

ORIGINAL_FILES_PATH = os.environ.get('ORIGINAL_FILES_PATH', '')
WEB_FILES_PATH = os.environ.get('WEB_FILES_PATH', '')

if not os.path.exists(ORIGINAL_FILES_PATH):
    os.makedirs(ORIGINAL_FILES_PATH)


ALLOWED_EXTENSIONS = set(['txt', 'pdf', 'png', 'jpg', 'jpeg', 'gif', 'oga', 'ogg', 'ogv', 'tif', 'tiff', 'heic',
                          'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'csv', 'zip', 'rar', '7z', 'mp4',
                          'mp3', 'wav', 'avi', 'mkv', 'flv', 'mov', 'wmv', 'm4a', 'mxf', 'cr2', 'arw', 'mts', 'nef', 'json', 'html', 'wma', 'aac', 'flac'])

mongodb = DatabaseHandler.DatabaseHandler()
cacheHandler = CacheHandler.CacheHandler()

def update_cache():
    get_total.invalidate_all()
    get_by_id.invalidate_all()


def parse_result(result):
    return json.loads(json_util.dumps(result))



def allowedFile(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def get_by_filters(body, current_user):
    try:
        # Buscar el recurso en la base de datos
        records = list(mongodb.get_all_records(
            'records', body['filters'], limit=20, skip=body['page'] * 20))
        # Si el recurso no existe, retornar error
        if not records:
            return {'msg': 'Recurso no existe'}, 404
        
        total = get_total(json.dumps(body['filters']))


        for r in records:
            r['id'] = str(r['_id'])
            r.pop('_id')
            r['total'] = total

        # registrar el log
        register_log(current_user, log_actions['record_get_all'], {
                     'filters': body['filters']})
        # retornar los records
        return parse_result(records), 200

    except Exception as e:
        return {'msg': str(e)}, 500
    
# Funcion para obtener el total de recursos
@cacheHandler.cache.cache(limit=1000)
def get_total(obj):
    try:
        # convertir string a dict
        obj = json.loads(obj)
        # Obtener el total de recursos
        total = mongodb.count('records', obj)
        # Retornar el total
        return total
    except Exception as e:
        raise Exception(str(e))
    
# Nuevos servicio para actualizar los campos displayName y accessRights de un record
def update_record(record, current_user):
    try:
        update = {}
        if 'displayName' in record:
            update['displayName'] = record['displayName']
        if 'accessRights' in record:
            if record['accessRights'] != 'public':
                update['accessRights'] = record['accessRights']
            else:
                update['accessRights'] = None

        update = FileRecordUpdate(**update)

        mongodb.update_record('records', {'_id': ObjectId(record['id'])}, update)
    except Exception as e:
        raise Exception(str(e))

# Nuevo servicio para borrar un parent de un record
def delete_parent(resource_id, parent_id, current_user):
    try:
        # Buscar el record en la base de datos
        record = mongodb.get_record('records', {'_id': ObjectId(parent_id)})

        # Si el record no existe, retornar error
        if not record:
            return {'msg': 'Record no existe'}, 404

        # Si el record no tiene el recurso como parent, retornar error
        if not any(x['id'] == resource_id for x in record['parent']):
            return {'msg': 'Record no tiene el recurso como parent'}, 404

        # Si el record tiene el recurso como parent, eliminarlo
        # el parent es de tipo dict y tiene los campos id y post_type
        record['parent'] = [x for x in record['parent']
                            if x['id'] != resource_id]
        
        array_parent = set(x['id'] for x in record['parent'])

        array_parents_temp = []
        # iterar sobre parent y en un nuevo array ir guardando los padres de cada parent
        for p in array_parent:
            r = mongodb.get_record('resources', {'_id': ObjectId(p)})

            if r:
                # se agregan los parents a array_parents si no estan ya en el array. Cada parent en el array_parents es del tipo {id: id, post_type: post_type}
                for parent in r['parents']:
                    array_parents_temp.append(parent)

        # se eliminan los parents que esten duplicados. Cada parent es del tipo {id: id, post_type: post_type}. Se eliminan los duplicados por id
        unique_array_parents = set(x['id'] for x in array_parents_temp)

        new_list = [next(item for item in array_parents_temp if item['id'] == id)
                    for id in unique_array_parents]
        array_parents = new_list

        status = record['status']
        # Si el record no tiene parents, cambiar el status a deleted
        if len(record['parent']) == 0:
            status = 'deleted'
            
        # Actualizar el record
        update = FileRecordUpdate(**{
            'parent': array_parent,
            'parents': array_parents,
            'status': status
        })

        mongodb.update_record('records', {'_id': ObjectId(parent_id)}, update)

        # Registrar el log
        register_log(current_user, log_actions['record_update'], {
                     'record': parent_id})
        # Limpiar la cache
        

        # Retornar el resultado
        return {'msg': 'Parent eliminado exitosamente'}, 200

    except Exception as e:
        print(str(e))
        return {'msg': str(e)}, 500


def update_parent(parent_id, current_user, parents):
    unique_array_parents = set(x['id'] for x in parents)

    new_list = [next(item for item in parents if item['id'] == id)
                for id in unique_array_parents]

    update = FileRecordUpdate(**{
        'parents': new_list
    })

    mongodb.update_record('records', {'_id': ObjectId(parent_id)}, update)

    # Registrar el log
    register_log(current_user, log_actions['record_update'], {
        'record': parent_id})
    # Limpiar la cache
    


# Nuevo servicio para crear un record para un recurso
def create(resource_id, current_user, files, upload = True, filesTags = None):
    # Buscar el recurso en la base de datos
    resource = mongodb.get_record('resources', {'_id': ObjectId(resource_id)}, fields={'parents': 1, 'post_type': 1})
    # Si el recurso no existe, retornar error
    if not resource:
        raise Exception('Recurso no existe')
    
    resp = []
    index = 0

    for f in files:
        if type(f) is not dict:
            filename = secure_filename(f.filename)
        else:
            filename = f['filename']
        
        if allowedFile(filename):
            if upload:
                # generar un nombre unico para el archivo
                filename_new = str(uuid.uuid4()) + '.' + \
                    filename.rsplit('.', 1)[1].lower()
                # coger la fecha actual y convertirla a string de la forma YYYY/MM/DD
                date = datetime.datetime.now().strftime("%Y/%m/%d")
                # hacer un path en base a la fecha actual
                path = os.path.join(ORIGINAL_FILES_PATH, date)
                # crear el directorio para guardar el archivo usando la ruta date
                if not os.path.exists(path):
                    os.makedirs(path)

                if type(f) is not dict:
                    f.save(os.path.join(path, filename))

                    f.flush()
                    os.fsync(f.fileno())
                else:
                    shutil.copy(f['file'], os.path.join(path, filename))

                # renombrar el archivo
                os.rename(os.path.join(path, filename),
                            os.path.join(path, filename_new))
                # calcular el hash 256 del archivo
                hash = hashlib.sha256()
                with open(os.path.join(path, filename_new), 'rb') as f:
                    for chunk in iter(lambda: f.read(4096), b""):
                        hash.update(chunk)

                # se verifica si el hash del archivo ya existe en la base de datos
                record = get_hash(str(hash.hexdigest()))
            else:
                record = None

            # si el record existe, se agrega el recurso como padre
            if record:
                # eliminar el archivo que se subio
                os.remove(os.path.join(path, filename_new))
                
                obj_resp = {
                    'id': str(record['_id']),
                    'tag': filesTags[index]['filetag']
                }
                if 'order' in filesTags[index]:
                    obj_resp['order'] = filesTags[index]['order']
                resp.append(obj_resp)

                new_parent = [{
                    'id': resource_id,
                    'post_type': resource['post_type']
                }, *record['parent']]

                new_parents = [*resource['parents'], *record['parents']]
                unique_array_parents = set(x['id'] for x in new_parents)
                new_list = [next(item for item in new_parents if item['id'] == id)
                            for id in unique_array_parents]
                new_parents = new_list

                update_dict = {
                    'parent': new_parent,
                    'parents': new_parents
                }

                if record['status'] == 'deleted':
                    if 'processing' in record:
                        if 'files' in record['processing']:
                            if len(record['processing']['files']) > 0:
                                update_dict['status'] = 'processed'
                            else:
                                update_dict['status'] = 'uploaded'
                        else:
                            update_dict['status'] = 'uploaded'
                    else:
                        update_dict['status'] = 'uploaded'

                # actualizar el record
                update = FileRecordUpdate(**update_dict)
                mongodb.update_record(
                    'records', {'_id': ObjectId(record['_id'])}, update)

                # registrar el log
                register_log(current_user, log_actions['record_update'], {
                                'record': str(record['_id'])})
                # limpiar la cache
                
            else:
                if upload:
                    # obtener el tamaño del archivo
                    size = os.path.getsize(os.path.join(path, filename_new))

                    # usar magic para obtener el tipo de archivo
                    mime = magic.from_file(os.path.join(
                        path, filename_new), mime=True)

                    # crear un nuevo record
                    record = FileRecord(**{
                        'name': filename,
                        'hash': str(hash.hexdigest()),
                        'size': size,
                        'filepath': str(os.path.join(date, filename_new)),
                        'mime': mime,
                        'parent': [{
                            'id': resource_id,
                            'post_type': resource['post_type']
                        }],
                        'parents': resource['parents'],
                        'status': 'uploaded'
                    })
                    # insertar el record en la base de datos
                    new_record = mongodb.insert_record('records', record)
                    obj_resp = {
                        'id': str(new_record.inserted_id),
                        'tag': filesTags[index]['filetag']
                    }
                    if 'order' in filesTags[index]:
                        obj_resp['order'] = filesTags[index]['order']
                    resp.append(obj_resp)
                else:
                    # crear un nuevo record
                    record = FileRecord(**{
                        'name': f['filename'],
                        'hash': f['hash'],
                        # 'size': size,
                        'filepath': f['path'],
                        'mime': f['mime'],
                        'parent': [{
                            'id': resource_id,
                            'post_type': resource['post_type']
                        }],
                        'parents': resource['parents'],
                        'status': 'uploaded'
                    })
                    # verificar que no exista un record con el mismo hash
                    record_exists = get_hash(f['hash'])

                    if not record_exists:
                        # insertar el record en la base de datos
                        new_record = mongodb.insert_record('records', record)
                        obj_resp = {
                            'id': str(new_record.inserted_id),
                            'tag': filesTags[index]['filetag']
                        }
                        if 'order' in filesTags[index]:
                            obj_resp['order'] = filesTags[index]['order']
                        resp.append(obj_resp)
                    else:
                        obj_resp = {
                            'id': str(new_record.inserted_id),
                            'tag': filesTags[index]['filetag']
                        }
                        if 'order' in filesTags[index]:
                            obj_resp['order'] = filesTags[index]['order']
                        resp.append(obj_resp)

                # registrar el log
                register_log(current_user, log_actions['record_create'], {'record': {
                    'name': record.name,
                    'hash': record.hash,
                    'size': record.size,
                    'filepath': record.filepath
                }})
                # limpiar la cache
                
                get_hash.invalidate_all()
        else:
            raise Exception('Tipo de archivo no permitido')

        index += 1
    # retornar el resultado
    return resp

@cacheHandler.cache.cache(limit=1000)
def get_hash(hash):
    try:
        # Buscar el recurso en la base de datos
        record = mongodb.get_record('records', {'hash': hash})
        # Si el recurso no existe, retornar error
        if not record:
            return None
        # retornar los records
        record['_id'] = str(record['_id'])
        return record

    except Exception as e:
        raise Exception(str(e))
    
# Nuevo servicio para obtener un record por su id verificando el usuario
@cacheHandler.cache.cache(limit=5000)
def get_by_id(id, current_user):
    try:
        # Buscar el record en la base de datos
        record = mongodb.get_record('records', {'_id': ObjectId(id)}, fields={'parent': 1, 'parents': 1, 'accessRights': 1, 'hash': 1, 'processing': 1, 'name': 1, 'displayName': 1, 'size': 1})

        # Si el record no existe, retornar error
        if not record:
            record = mongodb.get_record('resources', {'_id': ObjectId(id)}, fields={'_id': 1})

            if not record:
                return {'msg': 'Record no existe'}, 404
            else:
                return parse_result(record), 200
        
        if 'accessRights' in record:
            if record['accessRights']:
                if not has_right(current_user, record['accessRights']) and not has_right(current_user, 'admin'):
                    return {'msg': 'No tiene permisos para acceder a este recurso'}, 401
        
        # get keys from record['processing']
        keys = {}
        fileProcessing = None
        if 'processing' in record:
            # iterate over processing keys in record['processing']
            for key in record['processing']:
                keys[key] = {}
                keys[key]['type'] = record['processing'][key]['type']

        record['processing'] = keys


        from app.api.types.services import get_icon

        if 'parent' in record:
            to_clean = []
            for p in record['parent']:
                r_ = mongodb.get_record('resources', {'_id': ObjectId(p['id'])}, fields={'metadata.firstLevel.title': 1, 'post_type': 1})
                if r_:
                    p['name'] = r_['metadata']['firstLevel']['title']
                    p['icon'] = get_icon(r_['post_type'])
                else:
                    to_clean.append(p['id'])

            record['parent'] = [x for x in record['parent'] if x['id'] not in to_clean]


        if 'parents' in record:
            record.pop('parents')

        # Si el record existe, retornar el record
        return parse_result(record), 200

    except Exception as e:
        print(str(e))
        return {'msg': str(e)}, 500
    
def get_by_index_gallery(body, current_user):
    try:
        if 'id' not in body:
            return {'msg': 'id no definido'}, 400
        if 'index' not in body:
            return {'msg': 'index no definido'}, 400
        
        resource = mongodb.get_record('resources', {'_id': ObjectId(body['id'])}, fields={'filesObj': 1})
        ids = []
        if 'filesObj' in resource:
            for r in resource['filesObj']:
                ids.append(r['id'])

        img = list(mongodb.get_all_records('records', {'_id': {'$in': [ObjectId(id) for id in ids]}, 'processing.fileProcessing.type': 'image'}, fields={'processing': 1}, sort=[('name', 1)]).skip(body['index']).limit(1))

        return get_by_id(str(img[0]['_id']), current_user)

    except Exception as e:
        return {'msg': str(e)}, 500
    
# Nuevo servicio para devolver un stream de un archivo por su id
def get_stream(id, current_user):
    try:
        resp_, status = get_by_id(id, current_user)
        if status != 200:
            return {'msg': resp_['msg']}, 500
        
        path, type = cache_get_record_stream(id)

        path = os.path.join(WEB_FILES_PATH, path)

        if type == 'video':
            path = path + '.mp4'
        elif type == 'audio':
            path = path + '.mp3'

        # retornar el archivo
        return send_file(path, as_attachment=False)

    except Exception as e:
        return {'msg': str(e)}, 500
    
def get_processing_metadata(id, slug, current_user):
    try:
        resp_, status = get_by_id(id, current_user)
        if status != 200:
            return {'msg': resp_['msg']}, 500
        
        resp = cache_get_processing_metadata(id, slug)

        return resp, 200
    except Exception as e:
        return {'msg': str(e)}, 500
    
# Nuevo servicio para devolver la transcripcion de un plugin
def get_transcription(id, slug, current_user):
    try:
        resp_, status = get_by_id(id, current_user)
        if status != 200:
            return {'msg': resp_['msg']}, 500
        
        resp = cache_get_record_transcription(id, slug)
        # Si el record existe, retornar el record
        return resp, 200

    except Exception as e:
        return {'msg': str(e)}, 500

# Nuevo servicio para devolver las paginas en baja de un documento por su id
def get_document(id, current_user):
    try:
        resp_, status = get_by_id(id, current_user)
        if status != 200:
            return {'msg': resp_['msg']}, 500
        return cache_get_record_document_detail(id)

    except Exception as e:
        return {'msg': str(e)}, 500

def get_document_pages(id, pages, size, current_user):
    try:
        resp_, status = get_by_id(id, current_user)
        if status != 200:
            return {'msg': resp_['msg']}, 500
        pages = json.dumps(pages)
        resp = cache_get_pages_by_id(id, pages, size)
        response = Response(json.dumps(resp).encode('utf-8'), mimetype='application/json', direct_passthrough=False)
        return response
    except Exception as e:
        return {'msg': str(e)}, 500
    
def get_document_gallery(id, pages, size, current_user):
    try:
        from app.api.resources.services import get_by_id as get_resource_by_id
        resp_, status = get_resource_by_id(id, current_user)
        if status != 200:
            return {'msg': resp_['msg']}, 500
        pages = json.dumps(pages)
        resp = cache_get_imgs_gallery_by_id(id, pages, size)
        response = Response(json.dumps(resp).encode('utf-8'), mimetype='application/json', direct_passthrough=False)
        return response
    except Exception as e:
        print(str(e))
        return {'msg': str(e)}, 500
    
def get_document_block_by_page(current_user, id, page, slug, block=None):
    try:
        resp_, status = get_by_id(id, current_user)
        if status != 200:
            return {'msg': resp_['msg']}, 500
        
        print(id, page, slug, block)
        return cache_get_block_by_page_id(id, page, slug, block, current_user)
    except Exception as e:
        return {'msg': str(e)}, 500
    
def postBlockDocument(current_user, obj):
    try:
        # get record with body['id']
        record = mongodb.get_record('records', {'_id': ObjectId(obj['id_doc'])})
        # if record exists
        if record:
            # get record['processing'] and update it
            processing = record['processing']

            if obj['type_block'] == 'blocks':
                processing[obj['slug']]['result'][obj['page'] - 1]['blocks'].append({
                    'bbox': obj['bbox'],
                    **obj['data']
                })
            
            update = {
                'processing': processing
            }

            update = FileRecordUpdate(**update)
            mongodb.update_record('records', {'_id': ObjectId(obj['id_doc'])}, update)

            cache_get_block_by_page_id.invalidate_all()
            return {'msg': 'Bloque actualizado'}, 201
        else:
            return {'msg': 'Record no existe'}, 404
    except Exception as e:
        return {'msg': str(e)}, 500

def updateBlockDocument(current_user, obj):
    try:
        # get record with body['id']
        record = mongodb.get_record('records', {'_id': ObjectId(obj['id_doc'])})
        # if record exists
        if record:
            # get record['processing'] and update it
            processing = record['processing']

            for k, val in obj['data'].items():
                if obj['type_block'] == 'blocks':
                    processing[obj['slug']]['result'][obj['page'] - 1]['blocks'][obj['index']][k] = val
            
            if obj['type_block'] == 'blocks':
                processing[obj['slug']]['result'][obj['page'] - 1]['blocks'][obj['index']]['bbox'] = obj['bbox']
            
            update = {
                'processing': processing
            }

            update = FileRecordUpdate(**update)
            mongodb.update_record('records', {'_id': ObjectId(obj['id_doc'])}, update)

            cache_get_block_by_page_id.invalidate_all()
            return {'msg': 'Bloque actualizado'}, 200
        else:
            return {'msg': 'Record no existe'}, 404
    except Exception as e:
        return {'msg': str(e)}, 500
    
def deleteBlockDocument(current_user, obj):
    try:
        # get record with body['id']
        record = mongodb.get_record('records', {'_id': ObjectId(obj['id_doc'])})
        # if record exists
        if record:
            # get record['processing'] and update it
            processing = record['processing']

            if obj['type_block'] == 'blocks':
                processing[obj['slug']]['result'][obj['page'] - 1]['blocks'].pop(obj['index'])
            
            update = {
                'processing': processing
            }

            update = FileRecordUpdate(**update)
            mongodb.update_record('records', {'_id': ObjectId(obj['id_doc'])}, update)

            cache_get_block_by_page_id.invalidate_all()
            return {'msg': 'Bloque eliminado'}, 200
        else:
            return {'msg': 'Record no existe'}, 404
    except Exception as e:
        return {'msg': str(e)}, 500
    
@cacheHandler.cache.cache(limit=2000)
def get_favCount(id):
    try:
        record = mongodb.get_record('records', {'_id': ObjectId(id)}, fields={'favCount': 1})
        if not record:
            return {'msg': 'Record no existe'}, 404
        return record['favCount']
    except Exception as e:
        raise Exception(str(e))
    
def add_to_favCount(id):
    try:
        update = {
            '$inc': {
                'favCount': 1
            }
        }
        update_ = FileRecordUpdate(**update)
        mongodb.update_record('records', {'_id': ObjectId(id)}, update_)
        get_favCount.invalidate(id)
    except Exception as e:
        raise Exception(str(e))
    
def remove_from_favCount(id):
    try:
        update = {
            '$inc': {
                'favCount': -1
            }
        }
        update_ = FileRecordUpdate(**update)
        mongodb.update_record('records', {'_id': ObjectId(id)}, update_)
        get_favCount.invalidate(id)
    except Exception as e:
        raise Exception(str(e))
    
def delete_transcription_segment(id, body, user):
    resp_, status = get_by_id(id, user)
    if status != 200:
        return {'msg': resp_['msg']}, 500
    
    slug = body['slug']
    index = body['index']

    record = mongodb.get_record('records', {'_id': ObjectId(id)}, fields={'processing': 1})
    if not record:
        return {'msg': 'Record no existe'}, 404
    if 'processing' not in record:
        return {'msg': 'El record no tiene transcripción'}, 404
    if slug not in record['processing']:
        return {'msg': 'El record no tiene transcripción'}, 404
    if record['processing'][slug]['type'] != 'av_transcribe':
        return {'msg': 'Record no ha sido procesado con el slug ' + slug}, 404
    
    segments = record['processing'][slug]['result']['segments']

    segments.pop(index)

    update = {
        'processing': record['processing']
    }

    update['processing'][slug]['result']['segments'] = segments

    update = FileRecordUpdate(**update)
    mongodb.update_record('records', {'_id': ObjectId(id)}, update)

    cache_get_record_transcription.invalidate(id, slug)

    return {'msg': 'Segmento eliminado'}, 200
    
def edit_transcription(id, body, user):
    resp_, status = get_by_id(id, user)
    if status != 200:
        return {'msg': resp_['msg']}, 500
    
    slug = body['slug']

    record = mongodb.get_record('records', {'_id': ObjectId(id)}, fields={'processing': 1})
    if not record:
        return {'msg': 'Record no existe'}, 404
    if 'processing' not in record:
        return {'msg': 'El record no tiene transcripción'}, 404
    if slug not in record['processing']:
        return {'msg': 'El record no tiene transcripción'}, 404
    if record['processing'][slug]['type'] != 'av_transcribe':
        return {'msg': 'Record no ha sido procesado con el slug ' + slug}, 404
    
    segments = record['processing'][slug]['result']['segments']

    updateSpeaker = False
    segments[body['index']]['text'] = body['text']
    if 'speaker' in body:
        if body['speaker'] != segments[body['index']]['speaker']:
            updateSpeaker = segments[body['index']]['speaker']
            
    if updateSpeaker:
        for segment in segments:
            if 'speaker' in segment:
                if segment['speaker'] == updateSpeaker:
                    segment['speaker'] = body['speaker']

    update = {
        'processing': record['processing']
    }

    update['processing'][slug]['result']['segments'] = segments

    update = FileRecordUpdate(**update)
    mongodb.update_record('records', {'_id': ObjectId(id)}, update)

    cache_get_record_transcription.invalidate(id, slug)

    return {'msg': 'Transcripción editada'}, 200