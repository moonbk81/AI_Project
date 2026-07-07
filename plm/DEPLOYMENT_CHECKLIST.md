# PLM Integration Deployment Checklist

## Pre-Deployment

### ✅ Environment Setup
- [ ] Set environment variables
  ```bash
  export PLM_KNOX_ID="your_knox_id"
  export PLM_APP_ID="your_app_id"
  ```
- [ ] Verify network connectivity to PLM server
  ```bash
  curl http://10.195.55.11:8080/plmapi/broker.do
  ```
- [ ] Confirm Knox ID and App ID are registered with PLM admin
- [ ] Verify user account has "Quality Viewer" permission

### ✅ Dependencies
- [ ] Install/update Python packages
  ```bash
  pip install -r requirements.txt
  ```
- [ ] Verify `requests>=2.31.0` is installed
  ```bash
  python -c "import requests; print(requests.__version__)"
  ```
- [ ] Check PyYAML is installed for config
  ```bash
  python -c "import yaml; print(yaml.__version__)"
  ```

### ✅ Configuration
- [ ] Review `plm/plm_config.yaml`
  - [ ] Update `base_url` if using production server
  - [ ] Verify `disable_proxy: true` (workplace has proxy)
  - [ ] Check division codes match your organization
  - [ ] Configure RAG settings if using RAG integration
  - [ ] Set logging level (INFO for production)

### ✅ Authentication Testing
- [ ] Test PLM connection
  ```bash
  cd plm
  python -c "
  from plm_api_client import PLMDefectAPIClient
  import os
  
  client = PLMDefectAPIClient(
      base_url='http://10.195.55.11:8080/plmapi/broker.do',
      knox_id=os.getenv('PLM_KNOX_ID'),
      app_id=os.getenv('PLM_APP_ID')
  )
  
  # Try getting defect code list
  response = client.get_defect_code_list()
  print('Connection successful!' if response.is_success() else 'Connection failed!')
  "
  ```

## Integration Points

### ✅ RAG Integration Setup (if enabled)
- [ ] Create collection in vector DB for PLM documents
  ```python
  # In your RAG initialization
  collection = chroma_client.create_collection(
      name="plm_defects",
      metadata={"hnsw:space": "cosine"}
  )
  ```
- [ ] Initialize PLM integration
  ```python
  from plm.plm_rag_integration import create_plm_integration
  plm = create_plm_integration()
  ```
- [ ] Test document conversion
  ```python
  docs = plm.fetch_and_convert_defects("25", ["P190404-00007"])
  print(f"Converted {len(docs)} documents")
  ```

### ✅ Dashboard Integration (if using Streamlit)
- [ ] Import dashboard components
  ```python
  from plm.plm_dashboard import (
      initialize_session_state,
      render_plm_section
  )
  ```
- [ ] Add to main app
  ```python
  initialize_session_state()
  render_plm_section()
  ```
- [ ] Test dashboard rendering
  ```bash
  streamlit run app.py --logger.level=debug
  ```

### ✅ AI Analysis Integration (if applicable)
- [ ] Create context builder
  ```python
  from plm.plm_rag_integration import PLMDefectContextBuilder
  builder = PLMDefectContextBuilder(integration)
  ```
- [ ] Test context building
  ```python
  context = builder.build_defect_context("P190404-00007")
  assert context is not None
  ```
- [ ] Update AI prompts to use PLM context

## Testing

### ✅ Unit Tests
- [ ] Test API client initialization
  ```bash
  cd plm
  python -c "from plm_api_client import PLMDefectAPIClient; print('✓ Import successful')"
  ```
- [ ] Test RAG integration
  ```bash
  python -c "from plm_rag_integration import create_plm_integration; print('✓ Import successful')"
  ```
- [ ] Test dashboard components
  ```bash
  python -c "from plm_dashboard import initialize_session_state; print('✓ Import successful')"
  ```

### ✅ Integration Tests
- [ ] Test defect retrieval
  ```python
  response = client.get_defect_info("25", ["P190404-00007"])
  assert response.is_success()
  assert len(response.result['defectList']) > 0
  ```
- [ ] Test RAG document conversion
  ```python
  docs = integration.fetch_and_convert_defects("25", ["P190404-00007"])
  assert len(docs) > 0
  assert docs[0].content is not None
  ```
- [ ] Test dashboard rendering (manual Streamlit test)

### ✅ Error Handling Tests
- [ ] Invalid defect code
  ```python
  response = client.get_defect_info("25", ["INVALID"])
  assert not response.is_success()
  ```
- [ ] Bad credentials
  ```python
  bad_client = PLMDefectAPIClient(
      base_url="...",
      knox_id="invalid",
      app_id="invalid"
  )
  response = bad_client.get_defect_code_list()
  assert not response.is_success()
  ```

## Deployment

### ✅ Code Deployment
- [ ] Review all PLM-related code changes
- [ ] Verify no credentials hardcoded (use env vars only)
- [ ] Check file permissions are correct
  ```bash
  ls -la plm/*.py  # Should be readable
  ls -la plm/plm_config.yaml  # Should be readable
  ```
- [ ] Run final tests before deploying
- [ ] Commit and push to main branch

### ✅ Server Deployment
- [ ] Update requirements.txt on server
  ```bash
  pip install -r requirements.txt
  ```
- [ ] Set environment variables on server
  ```bash
  export PLM_KNOX_ID="..."
  export PLM_APP_ID="..."
  ```
- [ ] Copy/update `plm/plm_config.yaml` if needed
- [ ] Test connection from server
- [ ] Restart application/dashboard service

### ✅ Production Verification
- [ ] Verify PLM API calls work in production
  ```bash
  python -c "from plm.plm_api_client import PLMDefectAPIClient; ..."
  ```
- [ ] Check logs for PLM-related errors
  ```bash
  tail -f logs/plm_api.log
  ```
- [ ] Monitor dashboard PLM section
- [ ] Test RAG integration with PLM data
- [ ] Verify AI analysis uses PLM context correctly

## Monitoring

### ✅ Logging Configuration
- [ ] Check `plm_config.yaml` logging level
  ```yaml
  logging:
    level: "INFO"
    file: "logs/plm_api.log"
  ```
- [ ] Ensure log directory exists
  ```bash
  mkdir -p logs
  ```
- [ ] Set up log rotation (optional)

### ✅ Performance Monitoring
- [ ] Monitor API response times
- [ ] Check cache effectiveness
- [ ] Track error rates
- [ ] Monitor memory usage for large batches

### ✅ Health Checks
- [ ] Add periodic connection tests
  ```python
  # In monitoring script
  try:
      response = client.get_defect_code_list()
      if response.is_success():
          print("✓ PLM API is healthy")
  except Exception as e:
      print(f"✗ PLM API error: {e}")
      # Send alert/notification
  ```

## Post-Deployment

### ✅ Documentation
- [ ] Update project README with PLM integration info
- [ ] Document any custom modifications
- [ ] Update team on new PLM features
- [ ] Share authentication setup guide

### ✅ Training
- [ ] Show team how to use PLM dashboard
- [ ] Explain RAG integration with PLM data
- [ ] Demonstrate defect registration flow
- [ ] Share troubleshooting guide

### ✅ Maintenance Plan
- [ ] Weekly: Check PLM logs for errors
- [ ] Monthly: Verify API limits not exceeded
- [ ] Quarterly: Review and update PLM configuration
- [ ] Annually: Update documentation and examples

## Rollback Plan

If issues occur:

### Quick Rollback
1. Disable PLM section in dashboard
   ```python
   # In app.py
   if False:  # Disable temporarily
       render_plm_section()
   ```
2. Comment out PLM RAG integration
   ```python
   # plm_docs = integration.fetch_and_convert_defects(...)
   ```
3. Remove PLM context from AI prompts

### Full Rollback
1. Revert PLM-related code changes
   ```bash
   git checkout HEAD~N -- plm/
   ```
2. Remove environment variables
   ```bash
   unset PLM_KNOX_ID
   unset PLM_APP_ID
   ```
3. Restart application

## Support Contacts

- **PLM Administrator**: [Contact info]
- **Network Team**: [For proxy/firewall issues]
- **Project Lead**: [For escalations]

## Sign-Off

- [ ] Development Complete
- [ ] Testing Complete
- [ ] Documentation Complete
- [ ] Deployment Approved
- [ ] Production Verified

**Deployed By**: ________________  
**Date**: ________________  
**Notes**: 

---

## Quick Reference

### Enable PLM in Streamlit App
```python
# In app.py or main streamlit file
import streamlit as st
from plm.plm_dashboard import initialize_session_state, render_plm_section

initialize_session_state()
render_plm_section()
```

### Enable PLM in RAG System
```python
from plm.plm_rag_integration import create_plm_integration

integration = create_plm_integration()
docs = integration.fetch_and_convert_defects("25", defect_codes)
# Add docs to your vector DB
```

### Test PLM Connection
```bash
cd plm
python -c "
from plm_api_client import PLMDefectAPIClient
import os

client = PLMDefectAPIClient(
    base_url='http://10.195.55.11:8080/plmapi/broker.do',
    knox_id=os.getenv('PLM_KNOX_ID'),
    app_id=os.getenv('PLM_APP_ID')
)

try:
    response = client.get_defect_code_list()
    print('✓ Connected!' if response.is_success() else '✗ Failed!')
except Exception as e:
    print(f'✗ Error: {e}')
"
```
