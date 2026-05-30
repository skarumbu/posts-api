import pytest
from azure.storage.blob import ContainerClient

# Explicit Azurite connection string — 'UseDevelopmentStorage=true' is deprecated
# in azure-storage-blob >= 12.24.0. Use the full explicit form instead.
AZURITE_CONN_STR = (
    "DefaultEndpointsProtocol=http;"
    "AccountName=devstoreaccount1;"
    "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
    "BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;"
)
TEST_CONTAINER = "posts-test"


@pytest.fixture
def container_client():
    client = ContainerClient.from_connection_string(AZURITE_CONN_STR, container_name=TEST_CONTAINER)
    client.create_container()
    yield client
    client.delete_container()
