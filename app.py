import json
import streamlit as st
import codecs
import html as html_mod
import re
from pathlib import Path

st.set_page_config(page_title="LLM Evaluation Portal", layout="wide")

DATA_PATH = Path(__file__).parent / "data.json"

@st.cache_data
def load_data():
    with open(DATA_PATH, encoding='utf-8') as f:
        return json.load(f)

data = load_data()

def get_content_html(items):
    if not items: return ""
    if isinstance(items, list):
        return "".join([f"• {i}<br>" for i in items])
    return str(items)

def decode_text(s):
    if not s: return ""
    if '\\' in s:
        s = s.replace('\\n', '\n').replace('\\t', '\t').replace('\\r', '\r')
        # Handle literal unicode escapes like \u2014
        s = re.sub(r'\\u([0-9a-fA-F]{4})', lambda m: chr(int(m.group(1), 16)), s)
    return s

def clean_noise(text):
    if not text: return ""
    # Clean up internal paths
    text = re.sub(r'/home/[^/]+/\.cache/claude-hfi/[^/]+/(A|B)/', r'\1/', text)
    
    # Handle chat-UI code artifacts: Language\nCopy\n1\n2\n3...
    # These often have line numbers interleaved or at the start.
    artifact_header = r'(?:\n|^)(?:python|plaintext|bash|sh|javascript|yaml|json|sql|html|css|cpp|c|rust|go)\s*Copy\s*'
    if re.search(artifact_header, text, re.IGNORECASE):
        # 1. Replace the header with a markdown code block start
        text = re.sub(r'(?:\n|^)(python|plaintext|bash|sh|javascript|yaml|json|sql|html|css|cpp|c|rust|go)\s*Copy\s*', r'\n```\1\n', text, flags=re.IGNORECASE)
        
        # 2. Strip lone line numbers (e.g., a line that is just "1", "2", etc.)
        text = re.sub(r'^\s*[\d\s]+\s*$\n?', '', text, flags=re.MULTILINE)
        
        # 3. Heal fragmented code: Join lines if they are very short or look like part of an expression
        def heal_lines(content):
            lines = content.split('\n')
            healed = []
            if not lines: return ""
            
            curr = lines[0]
            for i in range(1, len(lines)):
                next_line = lines[i].strip()
                # Heuristic: If current line is short and doesn't look like a comment/ending
                # OR if it ends in an operator/bracket, join with next
                if (len(curr.strip()) < 15 or (curr.strip() and curr.strip()[-1] in '(=+-*/,')) and next_line and not next_line.startswith('#'):
                    curr = curr.rstrip() + " " + next_line
                else:
                    healed.append(curr)
                    curr = lines[i]
            healed.append(curr)
            return '\n'.join(healed)
            
        # We only apply healing inside the code block part
        if '```' in text:
            parts = text.split('```')
            for i in range(1, len(parts), 2):
                parts[i] = heal_lines(parts[i])
            text = '```'.join(parts)
            
        # 4. Ensure the code block is closed
        if text.count('```') % 2 != 0:
            text += '\n```'

    # NEW: Prevent accidental Setext headings (text followed by a lone hyphen/equals line).
    # This identifies a line of text followed by a line that is just hyphens or equals, 
    # and inserts a blank line to stop the markdown parser from creating a heading.
    text = re.sub(r'([^\n])\n([-=]+)$', r'\1\n\n\2', text, flags=re.MULTILINE)
    
    return text.strip()

def render_claude_response(resp_text):
    if not resp_text: return
    
    resp = decode_text(resp_text)
    
    # Comprehensive pattern for start/end tags, results, and errors
    tag_pattern = r'(<antml:thinking(?:>| )|antml:thinking|</antml:thinking>|<function_results>|</function_results>|<antml:invoke name=".*?">|</antml:invoke>|<antml:function_calls>|antml:function_calls|</antml:function_calls>|<tool_use_error>|</tool_use_error>)'
    parts = re.split(tag_pattern, resp, flags=re.DOTALL)
    
    state = "prose" # prose, thinking, results, invoke, error
    content_buffer = []
    current_tool = "Tool"

    def flush():
        nonlocal content_buffer
        if not content_buffer: return
        
        raw_text = "".join(content_buffer).strip()
        if not raw_text: 
            content_buffer = []
            return
            
        cleaned = clean_noise(raw_text)
        
        if state == "thinking":
            if cleaned:
                with st.expander("Thinking", expanded=False):
                    st.markdown(cleaned)
        elif state == "results":
            if cleaned:
                st.markdown(f'''
                <div class="terminal-window">
                    <div class="terminal-titlebar">
                        <div class="terminal-dot red"></div>
                        <div class="terminal-dot yellow"></div>
                        <div class="terminal-dot green"></div>
                        <span class="terminal-title">Execution Output</span>
                    </div>
                    <div class="terminal-body">{html_mod.escape(cleaned)}</div>
                </div>
                ''', unsafe_allow_html=True)
        elif state == "error":
            if cleaned:
                st.markdown(f'''
                <div class="terminal-window error-border">
                    <div class="terminal-titlebar error-bg">
                        <div class="terminal-dot red"></div>
                        <div class="terminal-dot yellow"></div>
                        <div class="terminal-dot green"></div>
                        <span class="terminal-title">System Error</span>
                    </div>
                    <div class="terminal-body error-text">{html_mod.escape(cleaned)}</div>
                </div>
                ''', unsafe_allow_html=True)
        elif state == "invoke":
            # Extract all parameters
            params_list = re.findall(r'<antml:parameter name="(.*?)">(.*?)</antml:parameter>', raw_text, re.DOTALL)
            params = {p[0]: p[1] for p in params_list}
            
            st.markdown(f'<div class="question-header" style="margin-top: 20px;">→ Executing {current_tool}</div>', unsafe_allow_html=True)
            
            if current_tool == 'Edit' and 'old_string' in params and 'new_string' in params:
                # Specialized Diff View for Edit tool
                file_path = params.get('file_path', 'Unknown File')
                st.markdown(f"**File**: `{file_path}`")
                
                old_lines = params['old_string'].strip().split('\n')
                new_lines = params['new_string'].strip().split('\n')
                
                diff_lines = []
                for line in old_lines:
                    diff_lines.append(f"- {line}")
                for line in new_lines:
                    diff_lines.append(f"+ {line}")
                
                diff_text = "\n".join(diff_lines)
                st.code(diff_text, language='diff')
                
                if 'replace_all' in params:
                    st.markdown(f"*Replace All: {params['replace_all']}*")
            
            elif current_tool in ['Bash', 'Read', 'Write', 'Grep', 'Agent']:
                # Primary tool handling
                payload = ""
                others = []
                primary_keys = ['command', 'file_path', 'content', 'pattern', 'prompt']
                
                for p_name, p_val in params.items():
                    if p_name in primary_keys:
                        payload = p_val.strip()
                    else:
                        others.append(f"**{p_name}**: `{p_val.strip()}`")
                
                if payload:
                    st.code(clean_noise(payload), language='bash' if current_tool == 'Bash' else 'python')
                if others:
                    st.markdown(" ".join(others))
            else:
                # Generic handling
                for p_name, p_val in params.items():
                    st.markdown(f"**{p_name}**")
                    st.code(clean_noise(p_val.strip()))
                    
            if not params and cleaned:
                st.code(cleaned)
        else:
            if cleaned:
                st.markdown(cleaned)
        
        content_buffer = []

    for part in parts:
        if not part: continue
        
        lower_part = part.lower()
        
        if '<antml:thinking' in lower_part or (lower_part.strip() == 'antml:thinking'):
            flush()
            state = "thinking"
        elif '</antml:thinking>' in lower_part:
            flush()
            state = "prose"
        elif '<function_results>' in lower_part:
            flush()
            state = "results"
        elif '</function_results>' in lower_part:
            flush()
            state = "prose"
        elif '<tool_use_error>' in lower_part:
            flush()
            state = "error"
        elif '</tool_use_error>' in lower_part:
            flush()
            state = "prose"
        elif '<antml:invoke' in lower_part:
            flush()
            state = "invoke"
            name_match = re.search(r'name="(.*?)"', part)
            current_tool = name_match.group(1) if name_match else "Tool"
        elif '</antml:invoke>' in lower_part:
            flush()
            state = "prose"
        elif 'antml:function_calls' in lower_part:
            flush()
        else:
            content_buffer.append(part)
            
    flush()

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700;800&display=swap');

    /* Base */
    html, body, [class*="css"] {
        font-family: 'Outfit', sans-serif;
        background-color: #ffffff;
        color: #1e1e1e;
    }

    h1, h2, h3, h4, h5, h6, p, span,
    div:not(.terminal-body):not(.terminal-titlebar):not(.terminal-window):not(.metric-tag) {
        color: #1e1e1e;
    }

    hr { border-color: #e5e7eb; }

    /* Streamlit Tab Accent */
    .stTabs [data-baseweb="tab-list"] button[aria-selected="true"] {
        border-bottom-color: #2563eb;
        color: #2563eb;
    }

    
    /* Custom containers */
    .minimal-box {
        border: 1px solid #e5e7eb;
        border-radius: 8px;
        padding: 1.5rem;
        margin-bottom: 1.5rem;
        background-color: #f9fafb;
    }

    .prompt-box {
        border-left: 4px solid #2563eb;
        padding-left: 1rem;
        margin-bottom: 1.5rem;
        background-color: #ffffff;
    }

    .rating-box {
        font-weight: bold;
        color: #2563eb;
        font-size: 1.1rem;
        margin: 1rem 0;
    }

    .justification-box {
        background-color: #ffffff;
        border: 1px solid #e5e7eb;
        padding: 1rem;
        border-radius: 4px;
        margin-top: 1rem;
        line-height: 1.6;
    }

    /* Side-by-side Comparison Boxes  */
    .comparison-container {
        display: flex;
        gap: 1.5rem;
        align-items: stretch;
        width: 100%;
        margin-bottom: 1.5rem;
    }

    .comparison-box {
        flex: 1;
        background-color: #f9fafb;
        padding: 1.5rem;
        border-radius: 12px;
        border: none;
        font-size: 0.95rem;
        line-height: 1.6;
        color: #374151;
    }

    .comparison-box.active { background-color: #1c83ff1a; }

    .comparison-box strong {
        display: block;
        margin-bottom: 0.75rem;
        color: #111827;
        font-size: 0.9rem;
        font-weight: 800;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }

    /*  Metric Tags (Pill Buttons)  */
    .metric-tag {
        display: inline-block;
        padding: 6px 14px;
        border-radius: 20px;
        font-size: 0.85rem;
        font-weight: 500;
        margin-right: 8px;
        margin-bottom: 8px;
        transition: all 0.2s ease;
    }

    .metric-tag.selected {
        background-color: #2563eb;
        color: #ffffff;
        border: 1px solid #2563eb;
        box-shadow: 0 2px 4px rgba(37, 99, 235, 0.2);
    }

    .metric-tag.unselected {
        background-color: #ffffff;
        color: #9ca3af;
        border: 1px solid #e5e7eb;
    }

    /*  Section Sub-headers  */
    .question-header {
        font-size: 0.95rem;
        font-weight: 600;
        color: #4b5563;
        margin-bottom: 12px;
    }

    /* Claude-CLI Style Terminal */
    .terminal-window {
        background-color: #1e1e1e;
        border-radius: 8px;
        overflow: hidden;
        margin: 15px 0;
        box-shadow: 0 4px 15px rgba(0,0,0,0.3);
        border: 1px solid #333;
    }
    .terminal-window.error-border {
        border: 1px solid #5a1d1d;
    }
    .terminal-titlebar {
        background-color: #333;
        padding: 8px 12px;
        display: flex;
        align-items: center;
        gap: 6px;
    }
    .terminal-titlebar.error-bg {
        background-color: #441111;
    }
    .terminal-title {
        color: #ddd !important;
        font-size: 12px;
        font-family: sans-serif;
        margin-left: 6px;
        font-weight: 500;
    }
    .terminal-dot {
        width: 12px;
        height: 12px;
        border-radius: 50%;
    }
    .red { background-color: #ff5f56; }
    .yellow { background-color: #ffbd2e; }
    .green { background-color: #27c93f; }
    
    .terminal-body {
        padding: 12px;
        color: #d4d4d4 !important;
        font-family: 'Fira Code', 'Roboto Mono', monospace;
        font-size: 13px;
        white-space: pre-wrap;
        line-height: 1.5;
        background-color: #1e1e1e;
    }
    .error-text {
        color: #f87171 !important;
    }
        margin-top: 10px;
    }

    .question-subtitle {
        font-size: 0.85rem;
        color: #6b7280;
        margin-bottom: 12px;
        margin-top: -10px;
    }

    .section-divider {
        margin: 25px 0 15px 0;
        border: 0;
        border-top: 1px solid #f3f4f6;
    }

    /* Evaluation Metric Rows (Radio Dots) */
    .metric-row {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 10px 0;
        border-bottom: 1px solid #eee;
    }

    .metric-label {
        font-weight: 500;
        color: #555;
        width: 250px;
    }

    .radio-container {
        display: flex;
        gap: 12px;
    }

    .radio-dot {
        width: 14px;
        height: 14px;
        border-radius: 50%;
        border: 2px solid #ddd;
        background: #f9f9f9;
    }

    .radio-dot.selected {
        background: #2563eb;
        border-color: #2563eb;
        box-shadow: 0 0 8px rgba(37, 99, 235, 0.4);
    }

    .radio-labels {
        display: flex;
        justify-content: space-between;
        width: 100%;
        font-size: 0.75rem;
        color: #999;
        margin-top: 4px;
    }

    /*  Terminal Response Viewer (Claude CLI style) */
    .terminal-window {
        border-radius: 10px;
        overflow: hidden;
        border: 1px solid #30363d;
        margin-bottom: 1rem;
        box-shadow: 0 4px 16px rgba(0, 0, 0, 0.2);
    }

    .terminal-titlebar {
        background-color: #161b22;
        padding: 10px 14px;
        display: flex;
        align-items: center;
        gap: 8px;
        border-bottom: 1px solid #30363d;
    }

    .terminal-dot {
        width: 12px;
        height: 12px;
        border-radius: 50%;
        display: inline-block;
    }

    .terminal-dot.red    { background-color: #ff5f56; }
    .terminal-dot.yellow { background-color: #ffbd2e; }
    .terminal-dot.green  { background-color: #27c93f; }

    .terminal-title {
        color: #8b949e;
        font-size: 0.75rem;
        font-family: 'Outfit', sans-serif;
        margin-left: 8px;
        letter-spacing: 0.03em;
    }

    .terminal-body,
    .terminal-body * {
        background-color: #0d1117;
        color: #c9d1d9;
        font-family: 'JetBrains Mono', 'Fira Code', 'Cascadia Code', 'Courier New', monospace;
    }

    .terminal-body {
        padding: 1.25rem 1.5rem;
        white-space: pre-wrap;
        word-break: break-word;
        font-size: 0.82rem;
        line-height: 1.65;
        max-height: 600px;
        overflow-y: auto;
    }

    .terminal-body::-webkit-scrollbar {
        width: 6px;
    }

    .terminal-body::-webkit-scrollbar-track {
        background: #0d1117;
    }

    .terminal-body::-webkit-scrollbar-thumb {
        background: #30363d;
        border-radius: 3px;
    }
</style>
""", unsafe_allow_html=True)

st.title("LLM Evaluation Portal")

# 2 tabs (one for each project)
project_names = [p["name"] for p in data["projects"]]
tabs = st.tabs(project_names)

for i, tab in enumerate(tabs):
    with tab:
        project = data["projects"][i]

        # Determine tasks
        task_names = [t["title"] for t in project["tasks"]]
        selected_task_name = st.selectbox(
            "Select Task",
            task_names,
            key=f"select_{project['id']}"
        )

        selected_task = next(t for t in project["tasks"] if t["title"] == selected_task_name)

        if selected_task["status"] == "pending":
            st.info("This task is pending and has no data yet.")
            continue

        st.markdown("---")

        # First thing: PR (Using task description)
        st.markdown("### Pull Request")
        st.write(selected_task["description"])

        # Iterate over turns
        turns = selected_task.get("turns", [])
        for turn in turns:
            st.markdown(f"## Turn {turn['turn_number']}")

            # Prompt
            st.markdown("#### Prompt")
            st.markdown(f'<div class="prompt-box">{turn["prompt"]}</div>', unsafe_allow_html=True)

            # Split responses
            st.markdown("#### Responses")
            c1, c2 = st.columns(2)

            model_a = turn.get("model_a", {})
            model_b = turn.get("model_b", {})

            with c1:
                st.markdown("##### Model A")
                if model_a.get("response"):
                    with st.expander("View Model A Response", expanded=False):
                        render_claude_response(model_a["response"])
            with c2:
                st.markdown("##### Model B")
                if model_b.get("response"):
                    with st.expander("View Model B Response", expanded=False):
                        render_claude_response(model_b["response"])

            pros_a = get_content_html(model_a.get("pros"))
            pros_b = get_content_html(model_b.get("pros"))
            cons_a = get_content_html(model_a.get("cons"))
            cons_b = get_content_html(model_b.get("cons"))

            # Pros Section
            if pros_a or pros_b:
                st.markdown("##### Pros")
                st.markdown(f"""
                <div class="comparison-container">
                    <div class="comparison-box {'active' if pros_a else ''}">
                        <strong>Model A</strong>
                        {pros_a if pros_a else '<i>No explicit pros provided.</i>'}
                    </div>
                    <div class="comparison-box {'active' if pros_b else ''}">
                        <strong>Model B</strong>
                        {pros_b if pros_b else '<i>No explicit pros provided.</i>'}
                    </div>
                </div>
                """, unsafe_allow_html=True)

            # Cons Section
            if cons_a or cons_b:
                st.markdown("##### Cons")
                st.markdown(f"""
                <div class="comparison-container">
                    <div class="comparison-box {'active' if cons_a else ''}">
                        <strong>Model A</strong>
                        {cons_a if cons_a else '<i>No explicit cons provided.</i>'}
                    </div>
                    <div class="comparison-box {'active' if cons_b else ''}">
                        <strong>Model B</strong>
                        {cons_b if cons_b else '<i>No explicit cons provided.</i>'}
                    </div>
                </div>
                """, unsafe_allow_html=True)

            # Senior Engineering Expectations
            if "senior_engineering_execution_expectations" in turn:
                st.markdown("---")
                st.markdown("#### Senior Engineering Execution Expectations")
                st.info(turn["senior_engineering_execution_expectations"])

            if "senior_engineering_behavioral_expectations" in turn:
                st.markdown("---")
                st.markdown("#### Senior Engineering Behavioral Expectations")
                st.info(turn["senior_engineering_behavioral_expectations"])

            # Multi-Axis Comparison
            multi_axis = turn.get("multi_axis", [])
            st.markdown("---")
            st.markdown("#### Detailed Evaluation Metrics")

            if multi_axis:
                # Get rating scale from data or use default
                labels = data.get("rating_scale", ["A much better", "A better", "A slightly better", "A barely better",
                                                  "B barely better", "B slightly better", "B better", "B much better"])
                scale_size = len(labels)

                for item in multi_axis:
                    dim = item["axis"]
                    val = item["rating"]

                    # Find index
                    idx = -1
                    if str(val).isdigit():
                        idx = int(val) - 1
                    else:
                        for j, l in enumerate(labels):
                            if l.lower() in str(val).lower():
                                idx = j
                                break

                    # Fallbacks for other scale naming conventions
                    if idx == -1:
                        if "tie (lean a)" in str(val).lower(): idx = (scale_size // 2) - 1
                        elif "tie (lean b)" in str(val).lower(): idx = (scale_size // 2)
                        elif "tie" in str(val).lower(): idx = (scale_size // 2) - 1

                    # Render row
                    radio_html = '<div class="radio-container">'
                    for ri in range(scale_size):
                        selected = "selected" if ri == idx else ""
                        radio_html += f'<div class="radio-dot {selected}"></div>'
                    radio_html += '</div>'

                    st.markdown(f"""
                    <div class="metric-row">
                        <div class="metric-label">{dim}</div>
                        <div>
                            {radio_html}
                            <div class="radio-labels">
                                <span>Model A</span>
                                <span>Model B</span>
                            </div>
                        </div>
                    </div>
                    """, unsafe_allow_html=True)
            else:
                st.write("N/A")

            # Evaluation Weights / Key Preference Axes
            weights = turn.get("evaluation_weights", [])
            if project["id"].lower() == "swordfish":
                st.markdown("---")
                st.markdown("#### Key Preference Axes")

                # Question 1: Preference Weighting
                st.markdown('<div class="question-header">Primary Drivers of Preference</div>', unsafe_allow_html=True)
                st.markdown('<p class="question-subtitle">Which individual axes held the most weight in the overall choice?</p>', unsafe_allow_html=True)
                q1_axes = ["Honesty", "Recklessness", "Decisionmaking", "Confidence", "Sycophancy", "Communication"]

                q1_tags = "".join([
                    f'<div class="metric-tag {"selected" if a in weights else "unselected"}">{a}</div>'
                    for a in q1_axes
                ])
                st.markdown(f'<div>{q1_tags}</div>', unsafe_allow_html=True)

                st.markdown('<hr class="section-divider">', unsafe_allow_html=True)

                # Question 2: Baseline Quality
                st.markdown('<div class="question-header" style="margin-top: 20px;">Baseline Quality Assessment</div>', unsafe_allow_html=True)
                st.markdown('<p class="question-subtitle">If the preference was minimal, what was the general quality of both models?</p>', unsafe_allow_html=True)
                q2_axes = ["Poor", "Great", "It\'s complicated"]

                q2_tags = "".join([
                    f'<div class="metric-tag {"selected" if a in weights else "unselected"}">{a}</div>'
                    for a in q2_axes
                ])
                st.markdown(f'<div>{q2_tags}</div>', unsafe_allow_html=True)

            # Detailed Feedback Sections (Marlin style)
            feedback = turn.get("feedback", {})
            if any(feedback.values()):
                st.markdown("---")
                st.markdown("#### Detailed Feedback")

                # Define core categories to split side-by-side
                categories = ["Agency", "Communication", "Solution Quality"]
                handled_keys = ["Expected Model Response", "Key Axes"]

                # Explicitly handle Key Axes before other categories
                key_axes_val = feedback.get("Key Axes") or turn.get("key_axes")
                if key_axes_val:
                    st.markdown("##### Key Axes")
                    st.info(key_axes_val)

                for cat in categories:
                    key_a = f"Model A {cat}"
                    key_b = f"Model B {cat}"

                    val_a = feedback.get(key_a, "")
                    val_b = feedback.get(key_b, "")

                    if val_a or val_b:
                        st.markdown(f"##### {cat}")

                        feedback_html = f"""
                        <div class="comparison-container">
                            <div class="comparison-box {'active' if val_a else ''}">
                                <strong>Model A</strong>
                                {val_a if val_a else '<i>N/A</i>'}
                            </div>
                            <div class="comparison-box {'active' if val_b else ''}">
                                <strong>Model B</strong>
                                {val_b if val_b else '<i>N/A</i>'}
                            </div>
                        </div>
                        """
                        st.markdown(feedback_html, unsafe_allow_html=True)
                        handled_keys.extend([key_a, key_b])

                # Handle any other feedback keys dynamically
                for key, value in feedback.items():
                    if value and key not in handled_keys:
                        st.markdown(f"**{key}**")
                        st.info(value)

            # Preference and Justification
            st.markdown("---")
            title = "Overall Preference" if project["id"].lower() == "swordfish" else "Overall Preference & Justification"
            st.markdown(f"#### {title}")

            # Winner and Rating Badge
            rating_text = f"Winner: {turn.get('winner', 'N/A')} | {turn.get('rating_label', '')} ({turn.get('rating', '?')}/{turn.get('max_rating', '?')})"
            st.markdown(f'<div class="rating-box">{rating_text}</div>', unsafe_allow_html=True)

            if project["id"].lower() != "swordfish":
                justification = turn.get("justification", "No justification provided.")
                st.markdown(f'<div class="justification-box">{justification}</div>', unsafe_allow_html=True)

            st.markdown("---")
            st.markdown("<br>", unsafe_allow_html=True)
