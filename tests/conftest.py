import pytest
from azure.storage.blob import ContainerClient

AZURITE_CONN_STR = "UseDevelopmentStorage=true"
TEST_CONTAINER = "posts-test"


@pytest.fixture
def container_client():
    client = ContainerClient.from_connection_string(AZURITE_CONN_STR, container_name=TEST_CONTAINER)
    client.create_container()
    yield client
    client.delete_container()
