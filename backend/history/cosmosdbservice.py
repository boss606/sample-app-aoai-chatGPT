import uuid
from datetime import datetime, timedelta
from azure.cosmos.aio import CosmosClient
from azure.cosmos import exceptions

class CosmosConversationClient():

    def __init__(self, cosmosdb_endpoint: str, credential: any, database_name: str, container_name: str, enable_message_feedback: bool = False):
        self.cosmosdb_endpoint = cosmosdb_endpoint
        self.credential = credential
        self.database_name = database_name
        self.container_name = container_name
        self.enable_message_feedback = enable_message_feedback
        try:
            self.cosmosdb_client = CosmosClient(self.cosmosdb_endpoint, credential=credential)
        except exceptions.CosmosHttpResponseError as e:
            if e.status_code == 401:
                raise ValueError("Invalid credentials") from e
            else:
                raise ValueError("Invalid CosmosDB endpoint") from e

        try:
            self.database_client = self.cosmosdb_client.get_database_client(database_name)
        except exceptions.CosmosResourceNotFoundError:
            raise ValueError("Invalid CosmosDB database name")

        try:
            self.container_client = self.database_client.get_container_client(container_name)
        except exceptions.CosmosResourceNotFoundError:
            raise ValueError("Invalid CosmosDB container name")


    async def ensure(self):
        if not self.cosmosdb_client or not self.database_client or not self.container_client:
            return False, "CosmosDB client not initialized correctly"
        try:
            database_info = await self.database_client.read()
        except:
            return False, f"CosmosDB database {self.database_name} on account {self.cosmosdb_endpoint} not found"

        try:
            container_info = await self.container_client.read()
        except:
            return False, f"CosmosDB container {self.container_name} not found"

        return True, "CosmosDB client initialized successfully"

    async def create_conversation(self, user_id, title = ''):
        now = datetime.utcnow()
        conversation = {
            'id': str(uuid.uuid4()),
            'type': 'conversation',
            'createdAt': now.isoformat(),
            'updatedAt': now.isoformat(),
            'expiresAt': (now + timedelta(days=30)).isoformat(),
            'active': True,
            'userId': user_id,
            'title': title
        }
        resp = await self.container_client.upsert_item(conversation)
        if resp:
            return resp
        else:
            return False

    async def upsert_conversation(self, conversation):
        resp = await self.container_client.upsert_item(conversation)
        if resp:
            return resp
        else:
            return False

    async def delete_conversation(self, user_id, conversation_id):
        """Soft-delete: marks active=False. Data preserved for auditing.
        Physical hard delete via CosmosDB TTL or a periodic cleanup job."""
        conversation = await self.get_conversation(user_id, conversation_id)
        if conversation:
            conversation['active'] = False
            conversation['updatedAt'] = datetime.utcnow().isoformat()
            await self.upsert_conversation(conversation)
        return True


    async def delete_messages(self, conversation_id, user_id):
        messages = await self.get_messages(user_id, conversation_id)
        response_list = []
        if messages:
            for message in messages:
                resp = await self.container_client.delete_item(item=message['id'], partition_key=user_id)
                response_list.append(resp)
            return response_list


    async def get_conversations(self, user_id, limit, sort_order = 'DESC', offset = 0):
        now = datetime.utcnow().isoformat()
        parameters = [
            {'name': '@userId', 'value': user_id},
            {'name': '@now', 'value': now}
        ]
        # Filter only active and non-expired conversations.
        # Support legacy documents without active/expiresAt fields via IS_DEFINED.
        # No ORDER BY to avoid a composite index in CosmosDB; sorting done in Python.
        query = """
            SELECT * FROM c
            WHERE c.userId = @userId
              AND c.type = 'conversation'
              AND (c.active = true OR NOT IS_DEFINED(c.active))
              AND (NOT IS_DEFINED(c.expiresAt) OR c.expiresAt > @now)
        """

        conversations = []
        async for item in self.container_client.query_items(query=query, parameters=parameters):
            conversations.append(item)

        # Sort in Python
        reverse = sort_order.upper() != 'ASC'
        conversations.sort(key=lambda c: c.get('updatedAt', c.get('createdAt', '')), reverse=reverse)

        # Apply offset/limit in Python
        if offset:
            conversations = conversations[offset:]
        if limit is not None:
            conversations = conversations[:limit]

        return conversations

    async def get_conversation(self, user_id, conversation_id):
        parameters = [
            {
                'name': '@conversationId',
                'value': conversation_id
            },
            {
                'name': '@userId',
                'value': user_id
            }
        ]
        query = "SELECT * FROM c WHERE c.id = @conversationId AND c.type='conversation' AND c.userId = @userId"
        conversations = []
        async for item in self.container_client.query_items(query=query, parameters=parameters):
            conversations.append(item)

        if len(conversations) == 0:
            return None
        else:
            return conversations[0]

    async def create_message(self, uuid, conversation_id, user_id, input_message: dict):
        message = {
            'id': uuid,
            'type': 'message',
            'userId' : user_id,
            'createdAt': datetime.utcnow().isoformat(),
            'updatedAt': datetime.utcnow().isoformat(),
            'conversationId' : conversation_id,
            'role': input_message['role'],
            'content': input_message['content']
        }

        if self.enable_message_feedback:
            message['feedback'] = ''

        resp = await self.container_client.upsert_item(message)
        if resp:
            ## update the parent conversation's updatedAt field
            conversation = await self.get_conversation(user_id, conversation_id)
            if not conversation:
                return "Conversation not found"
            conversation['updatedAt'] = message['createdAt']
            await self.upsert_conversation(conversation)
            return resp
        else:
            return False

    async def update_message_feedback(self, user_id, message_id, feedback):
        message = await self.container_client.read_item(item=message_id, partition_key=user_id)
        if message:
            message['feedback'] = feedback
            resp = await self.container_client.upsert_item(message)
            return resp
        else:
            return False

    async def get_messages(self, user_id, conversation_id):
        parameters = [
            {
                'name': '@conversationId',
                'value': conversation_id
            },
            {
                'name': '@userId',
                'value': user_id
            }
        ]
        # No ORDER BY to avoid composite index requirements; sort in Python by createdAt.
        query = "SELECT * FROM c WHERE c.conversationId = @conversationId AND c.type='message' AND c.userId = @userId"
        messages = []
        async for item in self.container_client.query_items(query=query, parameters=parameters):
            messages.append(item)

        messages.sort(key=lambda m: m.get('createdAt', ''))
        return messages
