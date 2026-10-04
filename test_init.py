import os, sys
sys.path.insert(0, '.')

# Check token
token = os.getenv('NEXUS_GITHUB_TOKEN')
print(f'NEXUS_GITHUB_TOKEN: {repr(token)}')

# Now let's test the full initialization flow
import sys

# Set the token for this session
os.environ['NEXUS_GITHUB_TOKEN'] = 'ghp_test_token_for_debug'

# Now import and test
from runtime.github_provider import initialize_github_connector_registration
conn, reg = initialize_github_connector_registration()
print(f'After init - connector.auth_state: {conn.auth_state}')
print(f'health: {conn.health()}')
disc = reg.discover('github.repository.read')
print(f'discover: auth_state={disc[0]["auth_state"] if disc else None}')