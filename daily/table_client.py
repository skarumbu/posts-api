"""
table_client.py — the one shared primitive the three daily/* modules use.

Separate connection string and storage account from the rest of posts-api
(POSTS_STORAGE_CONNECTION_STRING/blob_storage.py): these three tables are a
different workload (frequent small writes to ephemeral state) and were
provisioned on a dedicated app setting, DAILY_STORAGE_CONNECTION_STRING, so a
future table only needs a name here, not copied connection setup.
"""
import os

from azure.data.tables import TableServiceClient, TableClient


def get_table_client(table_name: str) -> TableClient:
    conn_str = os.environ["DAILY_STORAGE_CONNECTION_STRING"]
    service = TableServiceClient.from_connection_string(conn_str)
    return service.get_table_client(table_name)
