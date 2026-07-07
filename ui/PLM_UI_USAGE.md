# PLM UI Module Usage Guide

## Overview

The `plm_ui.py` module provides Streamlit UI components for PLM defect management, following the same pattern as other UI modules in the project (`crash_ui.py`, `network_ui.py`, etc).

## Available Components

### Main Section Renderer

#### `render_plm_section()`
Renders the complete PLM section with tabs for all operations.

```python
from ui import render_plm_section

render_plm_section()
```

Shows tabs for:
- 🔍 Search - Find and view defects
- 📊 Analysis - Analyze problem-solution mapping
- ➕ Register - Create new defects
- 💬 Comment - Manage comments

### Individual Component Renderers

#### `render_plm_search()`
Search for defects by code or ID with detailed viewing.

```python
from ui.plm_ui import render_plm_search

render_plm_search()
```

Features:
- Search by defect code or ID
- View summary table
- Expand for detailed information
- Support for multiple defects (comma-separated)

#### `render_plm_analyze()`
Analyze defect solutions and track problem-solution flow.

```python
from ui.plm_ui import render_plm_analyze

render_plm_analyze()
```

Features:
- Problem-Solution mapping
- Version tracking
- Timeline view
- Owner information

#### `render_plm_register()`
Register new defects via the dashboard.

```python
from ui.plm_ui import render_plm_register

render_plm_register()
```

Features:
- Full defect registration form
- Support for Draft and Open status
- Optional advanced fields
- Auto-generated external IDs

#### `render_plm_comment()`
Add, modify, or delete comments on defects.

```python
from ui.plm_ui import render_plm_comment

render_plm_comment()
```

Features:
- Save new comments
- Modify existing comments
- Delete comments
- Requires comment ID for modify/delete

### Sidebar Component

#### `render_plm_sidebar_stats()`
Shows PLM connection status in sidebar.

```python
from ui.plm_ui import render_plm_sidebar_stats

render_plm_sidebar_stats()
```

Shows:
- Connection status
- Cache refresh button

## Integration in Main App

### Full Integration

Add PLM section to your main Streamlit app:

```python
import streamlit as st
from ui import render_plm_section, render_plm_sidebar_stats

st.set_page_config(layout="wide", page_title="AI Analysis Dashboard")

# Main content
tab1, tab2, tab3 = st.tabs(["Telephony", "Network", "PLM"])

with tab1:
    render_telephony()

with tab2:
    render_network()

with tab3:
    render_plm_section()

# Sidebar
render_plm_sidebar_stats()
```

### Partial Integration

Add only specific components:

```python
import streamlit as st
from ui.plm_ui import render_plm_search, render_plm_analyze

st.header("Defect Management")

col1, col2 = st.columns(2)

with col1:
    render_plm_search()

with col2:
    render_plm_analyze()
```

### Dynamic Integration

Add PLM section conditionally:

```python
import streamlit as st
from ui import render_plm_section

if st.sidebar.checkbox("Enable PLM"):
    render_plm_section()
else:
    st.info("PLM module disabled")
```

## Configuration

All PLM UI components use configuration from `plm/plm_config.yaml`:

```yaml
plm:
  base_url: "http://10.195.55.11:8080/plmapi/broker.do"
  knox_id: "${PLM_KNOX_ID}"
  app_id: "${PLM_APP_ID}"

dashboard:
  enabled: true
  refresh_interval: 60

logging:
  level: "INFO"
```

## Environment Variables

Required:
```bash
export PLM_KNOX_ID="your_knox_id"
export PLM_APP_ID="your_app_id"
```

## API Error Handling

All components include built-in error handling:

```python
try:
    response = client.get_defect_info(...)
    if response.is_success():
        # Display results
    else:
        st.error(f"Error: {response.get_error_message()}")
except PLMAPIException as e:
    st.error(f"API Error: {e}")
except Exception as e:
    st.error(f"Unexpected Error: {e}")
```

## Session State Management

Components automatically manage Streamlit session state:

```python
# Initialized automatically by components
st.session_state.plm_integration       # PLM API client
st.session_state.plm_available         # Connection status
st.session_state.plm_cache             # Data cache
```

## Usage Patterns

### Pattern 1: Search and Register

```python
col1, col2 = st.columns(2)

with col1:
    st.subheader("Search Defects")
    render_plm_search()

with col2:
    st.subheader("Register New")
    render_plm_register()
```

### Pattern 2: Analysis Dashboard

```python
st.header("Defect Analysis Dashboard")

col1, col2 = st.columns(2)

with col1:
    st.subheader("Search & Details")
    render_plm_search()

with col2:
    st.subheader("Problem-Solution Analysis")
    render_plm_analyze()

st.divider()
st.subheader("Discussion")
render_plm_comment()
```

### Pattern 3: Workflow Management

```python
tab1, tab2, tab3 = st.tabs(["Intake", "Tracking", "Resolution"])

with tab1:
    st.subheader("Register Issues")
    render_plm_register()

with tab2:
    st.subheader("Search & Monitor")
    render_plm_search()

with tab3:
    st.subheader("Analyze Solutions")
    render_plm_analyze()
```

## Customization

### Adding Custom Styling

```python
import streamlit as st
from ui.plm_ui import render_plm_search

st.markdown("""
<style>
    .plm-container {
        background-color: #f0f2f6;
        padding: 20px;
        border-radius: 10px;
    }
</style>
""", unsafe_allow_html=True)

with st.container():
    render_plm_search()
```

### Conditional Rendering

```python
from ui.plm_ui import render_plm_section

user_role = st.sidebar.selectbox("Role", ["Viewer", "Editor", "Admin"])

if user_role in ["Editor", "Admin"]:
    render_plm_section()
else:
    render_plm_search()
```

## Performance Tips

1. **Enable Caching**
   ```python
   @st.cache_resource
   def get_plm_integration():
       from plm import create_plm_integration
       return create_plm_integration()
   ```

2. **Lazy Load Components**
   ```python
   if st.session_state.get('show_plm_detail'):
       render_plm_analyze()
   ```

3. **Batch Operations**
   - Search up to 99 defects at once
   - Reduces API calls

## Troubleshooting

### "PLM API is not configured"

```bash
# Check environment variables
echo $PLM_KNOX_ID
echo $PLM_APP_ID

# Set if missing
export PLM_KNOX_ID="your_id"
export PLM_APP_ID="your_app"

# Restart Streamlit
streamlit run app.py
```

### "Defect not found"

- Verify defect code format: `P190404-00007`
- Check division code matches defect
- Ensure user has "Quality Viewer" permission

### "Connection timeout"

- Check network connectivity
- Increase timeout in `plm_config.yaml`:
  ```yaml
  plm:
    timeout: 60
  ```

## API Reference

See `plm/PLM_API_README.md` for complete API documentation.

See `plm/INTEGRATION_GUIDE.md` for integration examples.

## Examples

### Example 1: Complete Dashboard Section

```python
import streamlit as st
from ui import render_plm_section, render_plm_sidebar_stats

st.set_page_config(layout="wide")

st.title("Defect Management System")

# Sidebar status
render_plm_sidebar_stats()

# Main content
render_plm_section()
```

### Example 2: Custom Workflow

```python
import streamlit as st
from ui.plm_ui import (
    render_plm_register,
    render_plm_search,
    render_plm_analyze,
    render_plm_comment
)

st.title("Support Ticket Workflow")

workflow_step = st.sidebar.selectbox(
    "Step",
    ["1. Register Issue", "2. Search", "3. Analyze", "4. Discuss"]
)

if workflow_step == "1. Register Issue":
    render_plm_register()
elif workflow_step == "2. Search":
    render_plm_search()
elif workflow_step == "3. Analyze":
    render_plm_analyze()
else:
    render_plm_comment()
```

### Example 3: Multi-Division Management

```python
import streamlit as st
from ui.plm_ui import render_plm_search

st.title("Multi-Division Defect View")

divisions = st.sidebar.multiselect(
    "Divisions",
    ["Mobile", "Network"],
    default=["Mobile"]
)

for division in divisions:
    with st.expander(f"🔍 {division} Division"):
        render_plm_search()
```

## Related Files

- **Core API**: `plm/plm_api_client.py`
- **Configuration**: `plm/plm_config.yaml`
- **Integration**: `plm/plm_rag_integration.py`
- **API Reference**: `plm/PLM_API_README.md`
- **Integration Guide**: `plm/INTEGRATION_GUIDE.md`

## Support

For issues:
1. Check `plm/PLM_API_README.md` for API details
2. Review `plm/INTEGRATION_GUIDE.md` for patterns
3. See `plm/DEPLOYMENT_CHECKLIST.md` for troubleshooting
