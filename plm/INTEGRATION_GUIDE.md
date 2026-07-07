# PLM Integration Guide

Complete guide for integrating PLM Defect Management API into AI_Project.

## Quick Start

### 1. Environment Setup

Set environment variables for PLM authentication:

```bash
export PLM_KNOX_ID="your_knox_id"
export PLM_APP_ID="your_app_id"
```

Or add to `.env` file:

```
PLM_KNOX_ID=your_knox_id
PLM_APP_ID=your_app_id
```

### 2. Configuration

Edit `plm/plm_config.yaml`:

```yaml
plm:
  base_url: "http://10.195.55.11:8080/plmapi/broker.do"
  knox_id: "${PLM_KNOX_ID}"           # Read from environment
  app_id: "${PLM_APP_ID}"             # Read from environment
  user_lang: "en"
```

### 3. Basic Usage

```python
from plm.plm_api_client import PLMDefectAPIClient, DivisionCode

# Initialize client
client = PLMDefectAPIClient(
    base_url="http://10.195.55.11:8080/plmapi/broker.do",
    knox_id="your_knox_id",
    app_id="your_app_id"
)

# Get defect information
response = client.get_defect_info(
    division_code=DivisionCode.MOBILE.value,
    defect_codes=["P190404-00007"]
)

if response.is_success():
    defects = response.result['defectList']
    for defect in defects:
        print(f"{defect['defectCode']}: {defect['plmTitle']}")
```

## Integration Points

### 1. RAG Integration (Defect Knowledge Base)

Use PLM defect data to enrich RAG knowledge:

```python
from plm.plm_rag_integration import create_plm_integration

# Create integration
integration = create_plm_integration()

# Fetch and convert defects to RAG documents
documents = integration.fetch_and_convert_defects(
    division_code="25",
    defect_codes=["P190404-00007", "P191014-00003"]
)

# Get RAG-compatible documents
rag_docs = integration.get_documents_for_rag()

# Insert into your RAG system (Chroma, etc.)
for doc in rag_docs:
    your_vector_db.add(
        ids=[doc['doc_id']],
        metadatas=[doc['metadata']],
        documents=[doc['content']],
        collection_name="plm_defects"
    )
```

### 2. Dashboard Integration

Add PLM section to Streamlit dashboard:

```python
# In your main app.py or streamlit app
import streamlit as st
from plm.plm_dashboard import (
    initialize_session_state,
    render_plm_section,
    render_plm_stats_sidebar
)

# Initialize
initialize_session_state()

# Render PLM section
render_plm_section()

# Add stats to sidebar
render_plm_stats_sidebar()
```

### 3. AI Analysis Enhancement

Use PLM context in AI analysis:

```python
from plm.plm_rag_integration import PLMDefectContextBuilder, create_plm_integration

# Initialize
integration = create_plm_integration()
builder = PLMDefectContextBuilder(integration)

# Build context for defect
context = builder.build_defect_context(
    defect_code="P190404-00007",
    division_code="25"
)

# Use in AI prompt
prompt = f"""
Analyze this defect:
- Title: {context['title']}
- Problem: {context['problem']}
- Root Cause: {context['root_cause']}
- Solution: {context['solution']}

Provide additional analysis...
"""
```

## File Structure

```
plm/
├── __init__.py                    # Package initialization
├── plm_api_client.py             # Core API client (900+ lines)
├── plm_api_example.py            # Usage examples
├── plm_config.yaml               # Configuration file
├── plm_rag_integration.py        # RAG integration module
├── plm_dashboard.py              # Streamlit dashboard component
├── PLM_API_README.md             # API documentation
├── INTEGRATION_GUIDE.md          # This file
└── LICENSE                       # License info
```

## API Methods Reference

### Read Operations

| Method | Purpose | Returns |
|--------|---------|---------|
| `get_defect_info()` | Get defect details | Dict with defectList |
| `get_defect_list()` | Get owner's defects | Dict with defectList |
| `get_defect_history()` | Get change history | Dict with history |
| `get_file_list()` | List attached files | Dict with fileList |
| `get_defect_code_list()` | Get available codes | Dict with code list |

### Write Operations

| Method | Purpose | Params |
|--------|---------|--------|
| `register_defect()` | Create new defect | DefectRegistrationRequest |
| `modify_defect()` | Update defect | DefectModifyRequest |
| `register_comment()` | Add/modify comment | CommentRegistrationRequest |
| `resolve_defect()` | Provide solution | reason, countermeasure |
| `upload_file()` | Attach file | file_path |

### Status Transitions

| Method | Purpose | Conditions |
|--------|---------|-----------|
| `draft_to_open()` | Draft → Open | Defect in Draft |
| `resolve_defect()` | Open → Resolve | Defect in Open |
| `reject_resolution()` | Resolve → Open | Solution not accepted |
| `close_defect()` | Resolve → Close | Solution accepted |
| `cancel_defect()` | Any → Cancelled | Duplicate or invalid |

## Configuration Options

### Basic Configuration

```yaml
plm:
  base_url: "http://10.195.55.11:8080/plmapi/broker.do"
  knox_id: "${PLM_KNOX_ID}"
  app_id: "${PLM_APP_ID}"
  user_lang: "en"
  timeout: 30
  disable_proxy: true
  max_retries: 3
```

### RAG Configuration

```yaml
rag:
  enabled: true
  data_types:
    - defect_info
    - resolutions
    - comments
    - history
  
  vector_db:
    enabled: true
    chunk_size: 500
    overlap: 50
  
  schedule:
    enabled: false
    interval_hours: 24
    batch_size: 50
```

### Dashboard Configuration

```yaml
dashboard:
  enabled: true
  plm_section: true
  refresh_interval: 60
  max_recent_defects: 10
  show_summary_stats: true
```

## Error Handling

### Common Errors

#### 1. Authentication Failed
```
Error: APP ID is not registered (PLM_API_01)
```
**Solution**: Verify PLM_KNOX_ID and PLM_APP_ID are correct and registered with PLM admin.

#### 2. Network Timeout
```
Error: API request failed: Connection timeout
```
**Solution**: Check network connectivity, increase timeout value in config.

#### 3. Defect Not Found
```
Response: success=false, message="Defect code not found"
```
**Solution**: Verify defect code format (e.g., P190404-00007), check division code.

### Error Handling Pattern

```python
from plm.plm_api_client import PLMAPIException

try:
    response = client.get_defect_info(
        division_code="25",
        defect_codes=["P190404-00007"]
    )
    
    if response.is_success():
        # Process result
        defects = response.result['defectList']
    else:
        # Handle API error
        error_msg = response.get_error_message()
        error_code = response.status.get('code')
        logger.error(f"API Error {error_code}: {error_msg}")
        
except PLMAPIException as e:
    # Handle request error
    logger.error(f"Request failed: {e}")
except Exception as e:
    # Handle unexpected error
    logger.error(f"Unexpected error: {e}")
```

## Integration Examples

### Example 1: Fetch Defect and Add to RAG

```python
from plm.plm_rag_integration import create_plm_integration

integration = create_plm_integration()

# Fetch defects
docs = integration.fetch_and_convert_defects(
    division_code="25",
    defect_codes=["P190404-00007"]
)

# Each doc can be added to your vector DB
for doc in docs:
    print(f"Defect: {doc.title}")
    print(f"Content: {doc.content}")
    print(f"Metadata: {doc.metadata}")
```

### Example 2: Enrich AI Analysis

```python
from plm.plm_rag_integration import PLMDefectContextBuilder, create_plm_integration

integration = create_plm_integration()
builder = PLMDefectContextBuilder(integration)

# Build batch context
batch_context = builder.build_batch_context(
    defect_codes=["P190404-00007", "P191014-00003"],
    division_code="25"
)

# Use in analysis
print(f"Total defects: {batch_context['total_defects']}")
print(f"Status distribution: {batch_context['statuses']}")
for doc in batch_context['documents']:
    # Process each defect
    pass
```

### Example 3: Dashboard Integration

```python
# In your Streamlit app
import streamlit as st
from plm.plm_dashboard import initialize_session_state, render_plm_section

st.set_page_config(layout="wide")

# Initialize PLM
initialize_session_state()

# Render PLM section
if st.session_state.get('plm_available'):
    render_plm_section()
else:
    st.warning("PLM not configured")
```

### Example 4: Register Defect from AI Analysis

```python
from plm.plm_api_client import DefectRegistrationRequest, DivisionCode

# Create defect from analysis results
request = DefectRegistrationRequest(
    divisionCode=DivisionCode.MOBILE.value,
    systemCode="AI_ANALYSIS",
    changeType="DRAFT",
    refObjectName="Galaxy S24",
    refObjectType="MFG",
    externalDefectId="AI_ANALYSIS_001",
    defectCategory="SW",
    createUser="ai_system",
    title="Auto-detected issue from log analysis",
    inChargeUser="owner_id",
    Content="Issue description from AI analysis",
    importance="B",
    occurRateType="Sometimes",
    occurPhase="DV",
    testUnit="S/W Engineering",
    testItem="Functional Test",
    functionBlock="Network",
    detailFunctionclass="Data Call"
)

response = client.register_defect(request)
if response.is_success():
    defect_code = response.result['defectCode']
    print(f"Defect registered: {defect_code}")
```

## Performance Optimization

### 1. Caching

```python
# Enable response caching in config
error_handling:
  cache_responses: true
  cache_ttl_hours: 24
```

### 2. Batch Processing

```python
# Fetch multiple defects at once (up to 99)
response = client.get_defect_info(
    division_code="25",
    defect_codes=[f"P19{i:06d}-{j:05d}" for i in range(10) for j in range(10)]
)
```

### 3. Async Integration

```python
import asyncio
from concurrent.futures import ThreadPoolExecutor

async def fetch_defects_async(codes):
    with ThreadPoolExecutor(max_workers=3) as executor:
        loop = asyncio.get_event_loop()
        tasks = [
            loop.run_in_executor(executor, lambda c=code: client.get_defect_info("25", [c]))
            for code in codes
        ]
        results = await asyncio.gather(*tasks)
    return results

# Usage
results = asyncio.run(fetch_defects_async(["P190404-00007", "P191014-00003"]))
```

## Testing

### Unit Testing

```python
import pytest
from plm.plm_api_client import PLMDefectAPIClient

def test_get_defect_info():
    client = PLMDefectAPIClient(
        base_url="http://test-server:8080/plmapi/broker.do",
        knox_id="test_user",
        app_id="TEST_APP"
    )
    
    response = client.get_defect_info(
        division_code="25",
        defect_codes=["P190404-00007"]
    )
    
    assert response.is_success()
    assert len(response.result['defectList']) > 0
```

### Integration Testing

```python
def test_plm_rag_integration():
    integration = create_plm_integration()
    
    docs = integration.fetch_and_convert_defects(
        division_code="25",
        defect_codes=["P190404-00007"]
    )
    
    assert len(docs) > 0
    assert docs[0].title is not None
    assert docs[0].content is not None
```

## Troubleshooting

### Connection Issues
- Check firewall rules allow access to PLM server
- Verify proxy settings (disable_proxy=true recommended)
- Test with curl: `curl http://10.195.55.11:8080/plmapi/broker.do`

### Authentication Issues
- Verify KNOX_ID and APP_ID in environment
- Check account hasn't been deactivated
- Contact PLM admin if access is denied

### Data Issues
- Verify defect codes are in correct format (P[YYMMDD]-[5 digits])
- Check division code matches defect's division
- Ensure user has 'Quality Viewer' permission

## Support

For issues or questions:

1. Check PLM API documentation: `PLM_API_README.md`
2. Review example code: `plm_api_example.py`
3. Check configuration: `plm_config.yaml`
4. Enable debug logging: `logging.level: DEBUG`
5. Contact PLM administration team

## Version History

- **v1.0.0** (2024-07): Initial release
  - Core API client with 20+ methods
  - RAG integration
  - Streamlit dashboard
  - Full documentation
