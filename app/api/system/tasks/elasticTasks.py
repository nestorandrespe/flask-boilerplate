from celery import shared_task
from app.utils import DatabaseHandler
from app.utils import IndexHandler
import os
from bson.objectid import ObjectId
from app.utils.index.spanish_settings import settings as spanish_settings
from app.api.types.services import get_metadata

index_handler = IndexHandler.IndexHandler()
mongodb = DatabaseHandler.DatabaseHandler()
ELASTIC_INDEX_PREFIX = os.environ.get('ELASTIC_INDEX_PREFIX', '')

def get_value_by_path(dict, path):
    try:
        keys = path.split('.')
        value = dict
        for key in keys:
            if key in value:
                value = value.get(key)
            else:
                value = None
                break

        return value

    except Exception as e:
        raise Exception(f'Error al obtener el valor del campo {key}')
    
def change_value(body, path, value):
    try:
        keys = path.split('.')
        temp = body
        for key in keys:
            if key not in temp:
                temp[key] = {}
            if key == keys[-1]:
                temp[key] = value
            else:
                temp = temp[key]
        return body
    except Exception as e:
        raise Exception(f'Error al cambiar el valor del campo {key}')

@shared_task(ignore_result=False, name='system.regenerate_index')
def regenerate_index_task(mapping, user):
    keys = index_handler.get_alias_indexes(
        ELASTIC_INDEX_PREFIX + '-resources').keys()
    if len(keys) == 1:
        name = list(keys)[0]
        number = name.split('_')[1]
        number = int(number) + 1
        new_name = ELASTIC_INDEX_PREFIX + '-resources_' + str(number)
        index_handler.create_index(
            new_name, settings=spanish_settings, mapping=mapping)
        index_handler.add_to_alias(
            ELASTIC_INDEX_PREFIX + '-resources', new_name)
        index_handler.reindex(name, new_name)
        index_handler.remove_from_alias(
            ELASTIC_INDEX_PREFIX + '-resources', name)
        index_handler.delete_index(name)

        return 'ok'
    else:
        index_handler.start_new_index(mapping)
        return 'ok'

@shared_task(ignore_result=False, name='system.index_resources')
def index_resources_task(body={}):
    skip = 0
    filters = {}
    loop = True
    if '_id' in body:
        filters['_id'] = ObjectId(body['_id'])

    resources = list(mongodb.get_all_records(
        'resources', filters, limit=1000, skip=skip))

    if body == {}:
        index_handler.delete_all_documents(ELASTIC_INDEX_PREFIX + '-resources')

    while len(resources) > 0 and loop:
        for resource in resources:
            document = {}
            post_type = resource['post_type']
            fields = get_metadata(post_type)['fields']
            for f in fields:
                if f['type'] != 'file' and f['type'] != 'simple-date':
                    destiny = f['destiny']
                    if destiny != '':
                        value = get_value_by_path(resource, destiny)
                        if value != None:
                            document = change_value(
                                document, f['destiny'], value)
                elif f['type'] == 'simple-date':
                    destiny = f['destiny']
                    if destiny != '':
                        value = get_value_by_path(resource, destiny)
                        if value != None:
                            value = value.strftime('%Y-%m-%d')
                            change_value(document, f['destiny'], value)

            document['post_type'] = post_type
            if 'parents' in resource:
                document['parents'] = resource['parents']
            if 'parent' in resource:
                document['parent'] = resource['parent']
            document['ident'] = resource['ident']
            document['status'] = resource['status']
            document['accessRights'] = 'public'
            document['files'] = len(
                resource['filesObj']) if 'filesObj' in resource else 0

            if 'accessRights' in resource:
                if resource['accessRights']:
                    document['accessRights'] = resource['accessRights']

            r = index_handler.index_document(
                ELASTIC_INDEX_PREFIX + '-resources', str(resource['_id']), document)
            if r.status_code != 201 and r.status_code != 200:
                raise Exception(
                    'Error al indexar el recurso ' + str(resource['_id']))

        if len(resources) < 1000:
            loop = False

        skip += 1000
        resources = list(mongodb.get_all_records(
            'resources', {}, limit=1000, skip=skip))

    return 'ok'

@shared_task(ignore_result=False, name='system.index_resources_delete')
def index_resources_delete_task(body={}):
    r = index_handler.delete_document(
        ELASTIC_INDEX_PREFIX + '-resources', body['_id'])
    if r['result'] != 'deleted':
        raise Exception('Error al indexar el recurso ' + str(body['_id']))

    return 'ok'