from . import MongoConector

# Esta clase sirve para manejar la base de datos siguiendo un esquema definido para los registros

class DatabaseHandler:
    _instance = None

    def __new__(cls, database_name):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.database_name = database_name
            cls._instance.mongo_conector = MongoConector.MongoConector(database_name)
            cls._instance.myclient = cls._instance.mongo_conector.get_client()
            cls._instance.mydb = cls._instance.myclient[database_name]
        return cls._instance

    # Esta función sirve para obtener todos los registros de una colección
    def get_all_records(self, collection, filters={}, sort=[], limit=0, skip=0, fields={}):
        if(len(sort) > 0):
            return self.mydb[collection].find(filters).sort(sort).limit(limit).skip(skip)
        else:
            return self.mydb[collection].find(filters).limit(limit).skip(skip)
    
    # Esta función sirve para obtener un registro de una colección
    def get_record(self, collection, filters={}):
        return self.mydb[collection].find_one(filters)
    
    # Esta función sirve para actualizar un registro de una colección dado un filtro y un modelo de actualización. El modelo de actualización debe ser un pydantic model
    def update_record(self, collection, filters, update_model):
        return self.mydb[collection].update_one(filters, {'$set': update_model.dict(exclude_unset=True)})
    
    # Esta función sirve para insertar un registro en una colección. El registro debe ser un pydantic model
    def insert_record(self, collection, record):
        return self.mydb[collection].insert_one(record.dict(exclude_unset=True))