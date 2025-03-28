import os
if "RENDER" in os.environ:
    os.system("playwright install chromium")
    os.system("playwright install-deps")
import re
import base64
import asyncio
import nest_asyncio
from urllib.parse import urljoin, urlparse

import dash
from dash import html, dcc, Input, Output, State
import dash_bootstrap_components as dbc

import requests
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
from html2markdown import convert

nest_asyncio.apply()

app = dash.Dash(__name__,
                external_stylesheets=[dbc.themes.CYBORG],
                suppress_callback_exceptions=True)
app.title = "LLMS Generator Tool"
server = app.server

# -----------------------------
# Utility Functions
# -----------------------------
def sanitize_text(text):
    """Sanitize and normalize text input."""
    if not text:
        return ""
    return ' '.join(text.strip().split())

def validate_url(url):
    """Validate and normalize URL."""
    try:
        parsed = urlparse(url)
        return parsed.scheme and parsed.netloc
    except Exception:
        return False

def get_homepage_info(url):
    """Extract homepage title and meta description."""
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        
        # Title extraction with fallbacks
        title = (
            soup.title.string.strip() if soup.title and soup.title.string 
            else soup.find('h1').text.strip() if soup.find('h1') 
            else "No Title"
        )
        
        # Meta description extraction with fallbacks
        meta_el = (
            soup.find("meta", {"name": "description"}) or 
            soup.find("meta", {"property": "og:description"})
        )
        meta = meta_el.get("content", "No Description").strip() if meta_el else "No Description"
        
        return sanitize_text(title), sanitize_text(meta)
    except Exception:
        return "No Title", "No Description"

async def extract_nav_async(homepage_url, age_gate_sel, cookie_sel, root_nav_selector, context_sel):
    """
    Enhanced navigation extractor with:
    - Dynamic content handling
    - Framework detection and robust selector fallbacks
    - Shadow DOM support
    - Optionally using a context selector to narrow down clickable elements
    """
    js_code = """
    function extractNavigation([rootSelector, contextSelector]) {
        // If a context selector is provided and non-empty, use it;
        // otherwise, default to anchors with href.
        const clickableSelector = (contextSelector && contextSelector.trim().length > 0) ? 
            contextSelector : 'a[href]';
        
        // Enhanced framework detection
        const frameworkDetectors = {
            react: () => !!document.querySelector('[data-reactroot], [data-reactid], [data-react], .ReactModal__Overlay'),
            vue: () => !!document.querySelector('[data-v-app], [data-vue], [v-], .v-application'),
            angular: () => !!document.querySelector('[ng-app], [ng-], [data-ng], .ng-scope'),
            svelte: () => !!document.querySelector('[data-svelte], [svelte-]'),
            nextjs: () => !!document.querySelector('[data-nextjs]'),
            gatsby: () => !!document.querySelector('[data-gatsby]')
        };
        
        const detectedFrameworks = Object.entries(frameworkDetectors)
            .filter(([_, detector]) => detector())
            .map(([name]) => name);
        
        // Special handling for known sites
        const isLego = window.location.hostname.includes('lego.com');
        const isShopify = window.location.hostname.includes('shopify.com') || 
                          !!document.querySelector('[data-shopify]');
        
        // Get all possible root elements with multiple fallbacks
        const getRootElements = () => {
            const selectors = [
                rootSelector,
                'nav', 
                'header nav',
                '[role="navigation"]',
                '[data-test*="nav"]',
                '[data-testid*="nav"]',
                '[aria-label*="navigation"]',
                '[class*="nav"]',
                '[id*="nav"]',
                isLego ? '[data-test="desktop-navigation"]' : null,
                isShopify ? '[data-section-type="header"]' : null
            ].filter(Boolean);
            
            return Array.from(document.querySelectorAll(selectors.join(',')));
        };
        
        // Enhanced node extraction with support for shadow DOM.
        const extractNodes = (element) => {
            const rootNode = element.getRootNode();
            const isShadow = rootNode !== document;
            
            // Get clickable elements using our clickableSelector.
            const links = Array.from(isShadow ? 
                rootNode.querySelectorAll(clickableSelector) : 
                element.querySelectorAll(clickableSelector));
            
            return links.map(link => {
                const node = { 
                    title: "", 
                    url: "", 
                    children: [] 
                };
                
                // Prefer aria-label or inner text for title.
                node.title = link.getAttribute('aria-label') || 
                             link.textContent.trim() || 
                             link.getAttribute('data-testid') || 
                             link.getAttribute('data-test') || 
                             link.getAttribute('title') || 
                             "Untitled Link";
                
                // Only set URL if it's an anchor.
                if (link.tagName === 'A') {
                    node.url = link.href;
                }
                
                // Check for nested menus (if element is within a list item).
                const parentLi = link.closest('li');
                let nestedMenu = null;
                if (parentLi) {
                    nestedMenu = parentLi.querySelector(':scope > ul');
                    // Additional framework-specific or site-specific checks.
                    if (!nestedMenu) {
                        if (detectedFrameworks.includes('react')) {
                            nestedMenu = parentLi.querySelector('[role="menu"], [aria-labelledby]');
                        } else if (detectedFrameworks.includes('vue')) {
                            nestedMenu = parentLi.querySelector('.submenu, .v-menu__content');
                        } else if (isLego) {
                            nestedMenu = parentLi.querySelector('[data-test="meganav-content"]');
                        } else if (isShopify) {
                            nestedMenu = parentLi.querySelector('.dropdown-menu, .meganav');
                        }
                    }
                }
                
                if (nestedMenu) {
                    node.children = extractNodes(nestedMenu);
                }
                
                return node;
            });
        };
        
        const roots = getRootElements();
        if (roots.length === 0) return [];
        
        const allNodes = [];
        roots.forEach(root => {
            allNodes.push(...extractNodes(root));
        });
        
        // Deduplicate nodes by URL.
        const uniqueNodes = [];
        const seenUrls = new Set();
        allNodes.forEach(node => {
            if (node.url && seenUrls.has(node.url)) return;
            uniqueNodes.push(node);
            if (node.url) seenUrls.add(node.url);
        });
        
        return uniqueNodes;
    }
    """
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        
        # Spoof navigator.webdriver for compatibility.
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """)
        
        page = await context.new_page()
        
        try:
            # Load the page.
            await page.goto(homepage_url, wait_until="networkidle", timeout=90000)
            await page.wait_for_timeout(3000)  # Allow extra time for dynamic content.
            
            # --- Navigation Toggle Handling ---
            toggle_selector = '.nav-toggle'  # Adjust as needed.
            try:
                toggle_button = await page.query_selector(toggle_selector)
                if toggle_button:
                    await toggle_button.click()
                    await page.wait_for_selector('.navigation-menu.expanded', timeout=5000)
                # End toggle handling.
            except Exception as e:
                print("Navigation toggle not found or error:", e)
            
            # Dismiss overlays (age gate, cookie notices, etc.).
            async def dismiss_overlays():
                selectors = [sel for sel in [age_gate_sel, cookie_sel] if sel]
                for selector in selectors:
                    try:
                        await page.click(selector, timeout=5000)
                        await page.wait_for_timeout(1000)
                    except:
                        continue
            await dismiss_overlays()
            
            # Try extraction with multiple attempts.
            tree = []
            max_attempts = 3
            for attempt in range(max_attempts):
                try:
                    tree = await page.evaluate(js_code, [root_nav_selector, context_sel])
                    if tree and len(tree) > 0:
                        break
                    await page.wait_for_timeout(2000)
                    if attempt == 1:
                        await page.keyboard.press('Tab')
                        await page.wait_for_timeout(500)
                        await page.keyboard.press('Enter')
                        await page.wait_for_timeout(1000)
                except Exception as e:
                    print(f"Attempt {attempt + 1} failed: {str(e)}")
                    if attempt == max_attempts - 1:
                        raise e
            
            return tree if tree else []
            
        except Exception as e:
            print(f"Extraction error: {str(e)}")
            return []
        finally:
            await browser.close()
# -----------------------------
# Helper: Format Navigation Tree as Markdown
# -----------------------------
def format_tree_md(tree, base_url, indent=0):
    """Enhanced markdown formatting for navigation tree."""
    md = ""
    prefix = "  " * indent
    for node in tree:
        # Enhanced filtering
        if not node.get("url") and not node.get("children"):
            continue
        
        # Title normalization and filtering
        title = sanitize_text(node.get("title", "Untitled"))
        
        # Skip uninformative titles
        if (len(title) < 2 or 
            title.lower() in ['more', 'menu', 'click here', 'home', 'new']):
            continue
        
        # URL processing
        if node.get("url"):
            try:
                full_url = urljoin(base_url, node.get("url"))
                parsed_url = urlparse(full_url)
                
                # Additional URL filtering
                if (not parsed_url.netloc or 
                    len(parsed_url.path) > 100 or 
                    any(x in parsed_url.path.lower() for x in ['#', '?', 'javascript'])):
                    continue
                
                md += f"{prefix}- [{title}]({full_url})\n"
            except Exception:
                continue
        else:
            md += f"{prefix}- {title}\n"
        
        # Recursive children processing
        if node.get("children"):
            md += format_tree_md(node["children"], base_url, indent + 1)
    
    return md

def convert_links_to_structured(input_text):
    """Convert various link formats to structured format."""
    import re
    
    # Different link extraction patterns
    patterns = [
        r'\[(.*?)\]\((.*?)\)',  # Markdown style: [text](url)
        r'https?://\S+',         # Raw URLs
        r'<a\s+href="(.*?)".*?>(.*?)</a>',  # HTML links
    ]
    
    links = []
    for pattern in patterns:
        links.extend(re.findall(pattern, input_text))
    
    # Deduplicate and structure
    unique_links = {}
    for link in links:
        # Handle different pattern match formats
        if isinstance(link, tuple):
            text, url = link
        else:
            text, url = link, link
        
        # Normalize and validate
        url = url.strip()
        text = sanitize_text(text) or url
        
        if validate_url(url):
            unique_links[url] = text
    
    return unique_links

def sanitize_filename(filename):
    """
    Create a clean, URL-safe filename.
    
    Converts to lowercase, replaces spaces and special characters,
    ensures filename length, and adds .md extension.
    """
    import re
    
    # Remove protocol and domain, keep path
    filename = re.sub(r'^https?://[^/]+', '', filename)
    
    # Replace problematic characters
    filename = re.sub(r'[^\w\-_\.]', '_', filename)
    
    # Remove consecutive underscores
    filename = re.sub(r'_+', '_', filename)
    
    # Trim filename length
    filename = filename[:200]
    
    # Ensure .md extension
    if not filename.endswith('.md'):
        filename += '.md'
    
    return filename.strip('_.')

def extract_key_content(soup):
    """
    Extract the most important content for LLM optimization.
    
    Prioritizes:
    - Main content area
    - Page description
    - First few paragraphs
    - Key headings
    """
    # Remove unnecessary elements
    for script in soup(["script", "style", "head", "header", "footer", "nav", "aside"]):
        script.decompose()
    
    # Try to find the main content area
    main_content = soup.find(['main', 'article', 'div.content', 'section.content'])
    if not main_content:
        main_content = soup.body
    
    # Extract key elements
    key_elements = []
    
    # Add page description if available
    meta_desc = soup.find('meta', {'name': 'description'})
    if meta_desc and meta_desc.get('content'):
        key_elements.append(f"# Page Description\n\n{meta_desc['content']}\n")
    
    # Add title
    title = soup.title.string if soup.title else "Untitled Page"
    key_elements.append(f"# {title}\n")
    
    # Extract key headings and paragraphs
    for heading in main_content.find_all(['h1', 'h2', 'h3']):
        key_elements.append(f"## {heading.get_text(strip=True)}\n")
    
    # Add first few paragraphs
    paragraphs = main_content.find_all('p', limit=5)
    for p in paragraphs:
        text = p.get_text(strip=True)
        if text:
            key_elements.append(f"{text}\n")
    
    return "\n".join(key_elements)

def process_webpage_to_markdown(url):
    """
    Process a webpage and convert its key content to Markdown.
    Returns a tuple of (filename, markdown_content)
    """
    try:
        # Fetch the webpage
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        
        # Parse with BeautifulSoup
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        # Extract key content
        md_content = extract_key_content(soup)
        
        # Generate filename
        filename = sanitize_filename(url)
        
        return filename, md_content
    
    except Exception as e:
        error_filename = sanitize_filename(url + '_error')
        return error_filename, f"Error processing {url}: {str(e)}"

def convert_urls_to_markdown(n_clicks, input_urls):
    if not input_urls:
        return "No URLs provided", True
    
    # Process URLs, handling both markdown and plain text input
    urls = []
    for line in input_urls.split('\n'):
        line = line.strip()
        # Extract URL from markdown or use line directly
        match = re.search(r'\[.*?\]\((.*?)\)', line)
        if match:
            urls.append(match.group(1))
        elif line.startswith(('http://', 'https://')):
            urls.append(line)
    
    # Track files for download
    processed_files = {}
    
    # Convert URLs to markdown
    converted_content = []
    for url in urls:
        filename, md_content = process_webpage_to_markdown(url)
        processed_files[filename] = md_content
        converted_content.append(f"File: {filename}\n{md_content}\n---\n")
    
    # Store processed files for download
    app.server.processed_markdown_files = processed_files
    
    return "\n".join(converted_content), False

def download_markdown_files(n_clicks):
    # Retrieve processed files
    processed_files = getattr(app.server, 'processed_markdown_files', {})
    
    if not processed_files:
        return None
    
    # If only one file, return single file
    if len(processed_files) == 1:
        filename, content = list(processed_files.items())[0]
        return dict(content=content, filename=filename)
    
    # Multiple files - create a zip
    import io
    import zipfile
    
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for filename, content in processed_files.items():
            zip_file.writestr(filename, content)
    
    zip_buffer.seek(0)
    return dict(content=zip_buffer.read(), filename="webpage_markdown_files.zip")

# -----------------------------
# Application Layout
# -----------------------------
def create_tooltip(message):
    """Create a tooltip component."""
    return dbc.Tooltip(message, placement="right")

app.layout = dbc.Container([
    html.H1("LLMS Generator Toolkit", className="mb-4 text-center"),
    
        dbc.Tabs([
        # Navigation Extraction Tab
        dbc.Tab(label="1. Extract Navigation Links (in markdown format)", tab_id="nav-tab", children=[
            dbc.Card([
                dbc.CardBody([
                    # Selector Guidance Accordion
                    # Selector Guidance Accordion
dbc.Accordion([
    dbc.AccordionItem(
        title="🔍 Selector Guidance (Click to Expand)",
        children=[
            html.Div([
                html.P("Precise selector targeting helps extract accurate navigation. Here are detailed examples and recommendations:"),
                
                # Root Navigation Selector Section
                html.H5("Root Navigation Selector (Mandatory)", className="mt-3"),
                html.P("This should target the main container of your navigation. Examples for different frameworks:"),
                dbc.ListGroup([
                    dbc.ListGroupItem([
                        html.Strong("Standard HTML:"),
                        html.Code("nav, header nav, #main-nav"),
                        create_tooltip("Basic CSS selectors for traditional websites")
                    ]),
                    dbc.ListGroupItem([
                        html.Strong("React:"),
                        html.Code('[data-testid="main-nav"], .Navbar_root__abc123'),
                        create_tooltip("Look for data-testid attributes or component-based class names")
                    ]),
                    dbc.ListGroupItem([
                        html.Strong("Vue:"),
                        html.Code('[data-vue="navigation"], .v-navigation-drawer'),
                        create_tooltip("Vue-specific attributes or Vuetify class names")
                    ]),
                    dbc.ListGroupItem([
                        html.Strong("Shopify:"),
                        html.Code('[data-section-type="header"], .site-nav'),
                        create_tooltip("Common Shopify theme selectors")
                    ]),
                    dbc.ListGroupItem([
                        html.Strong("WordPress:"),
                        html.Code('#main-menu, .menu-primary-container'),
                        create_tooltip("Typical WordPress theme selectors")
                    ])
                ], flush=True, className="mb-3"),
                
                # Age Gate Selector Section
                html.H5("Age Gate Selector (Optional)", className="mt-3"),
                html.P("For sites with age verification (alcohol, tobacco, etc.):"),
                dbc.ListGroup([
                    dbc.ListGroupItem([
                        html.Strong("Common patterns:"),
                        html.Code('button.age-gate-submit, #age-verify-button, [data-test="age-gate-accept"]'),
                        create_tooltip("Look for buttons with 'age', 'verify', or 'submit' in class/ID")
                    ]),
                    dbc.ListGroupItem([
                        html.Strong("Example from LEGO:"),
                        html.Code('[data-test="age-gate-accept"]'),
                        create_tooltip("Found using browser dev tools")
                    ])
                ], flush=True, className="mb-3"),
                
                # Cookie Selector Section
                html.H5("Cookie Selector (Optional)", className="mt-3"),
                html.P("For cookie consent banners that might obscure navigation:"),
                dbc.ListGroup([
                    dbc.ListGroupItem([
                        html.Strong("Common patterns:"),
                        html.Code('#cookie-accept, .js-cookie-consent-agree, [aria-label="Accept cookies"]'),
                        create_tooltip("Target accept/agree buttons, not the whole banner")
                    ]),
                    dbc.ListGroupItem([
                        html.Strong("GDPR-compliant sites:"),
                        html.Code('[onclick*="cookie"], button:contains("Accept")'),
                        create_tooltip("Look for onclick handlers with 'cookie' in them")
                    ])
                ], flush=True, className="mb-3"),
                
                # Context Selector Section
                html.H5("Context Selector (Optional)", className="mt-3"),
                html.P("Refine which elements within the navigation should be considered links:"),
                dbc.ListGroup([
                    dbc.ListGroupItem([
                        html.Strong("Basic links:"),
                        html.Code('a[href], [role="link"]'),
                        create_tooltip("Standard anchor tags or ARIA link roles")
                    ]),
                    dbc.ListGroupItem([
                        html.Strong("JavaScript frameworks:"),
                        html.Code('[data-test="nav-link"], .router-link'),
                        create_tooltip("Framework-specific link components")
                    ]),
                    dbc.ListGroupItem([
                        html.Strong("Mega menus:"),
                        html.Code('.mega-menu a, .dropdown-item'),
                        create_tooltip("For complex navigation structures")
                    ])
                ], flush=True),
                
                # Pro Tips Section
                html.H5("Pro Tips", className="mt-3"),
                dbc.Alert([
                    html.Ul([
                        html.Li("Use browser DevTools (F12) to inspect elements and test selectors"),
                        html.Li("Right-click an element → Copy → Copy selector (Chrome/Firefox)"),
                        html.Li("Start with broad selectors (like 'nav') then refine as needed"),
                        html.Li("For SPAs, look for data-testid or data-qa attributes"),
                        html.Li(["For a working example: ",html.Strong("1) Click 'Load LEGO Example'"),html.Br(),html.Strong("2) Click 'Extract Navigation'")," to see it work with all fields pre-configured"])
                    ])
                ], color="info")
            ])
        ]
    )
], start_collapsed=True, className="mb-3"),
                    
                    # Add this right after the Selector Guidance accordion
                    dbc.Row([
                        dbc.Col(
                            dbc.Button(
                                "🔄 Load LEGO Example",
                                id="load-lego-example",
                                color="info",
                                className="w-100 mb-3",
                                outline=True
                            )
                        )
                    ]),

                    # Input Form
                    dbc.Form([
                        dbc.Row([
                            dbc.Col([
                                dbc.Label("Homepage URL"),
                                dcc.Input(
                                    id="homepage-url",
                                    type="url",
                                    placeholder="https://example.com",
                                    className="form-control mb-2",
                                    required=True
                                )
                            ], width=6),
                            dbc.Col([
                                dbc.Label("Root Navigation Selector"),
                                dcc.Input(
                                    id="root-nav-selector",
                                    type="text",
                                    placeholder="nav, header nav, .main-navigation",
                                    className="form-control mb-2"
                                )
                            ], width=6)
                        ]),
                        
                        # Optional Selectors
                        dbc.Row([
                            dbc.Col([
                                dbc.Label("Age Gate Selector (Optional)"),
                                dcc.Input(
                                    id="age-gate-selector",
                                    type="text",
                                    placeholder="button.age-gate-close",
                                    className="form-control mb-2"
                                )
                            ], width=4),
                            dbc.Col([
                                dbc.Label("Cookie Selector (Optional)"),
                                dcc.Input(
                                    id="cookie-selector",
                                    type="text",
                                    placeholder="button.cookie-accept",
                                    className="form-control mb-2"
                                )
                            ], width=4),
                            dbc.Col([
                                dbc.Label("Context Selector (Optional)"),
                                dcc.Input(
                                    id="context-selector",
                                    type="text",
                                    placeholder="a.nav-link",
                                    className="form-control mb-2"
                                )
                            ], width=4)
                        ]),
                        
                        # Action Buttons
                        dbc.Row([
                            dbc.Col([
                                dbc.Button(
                                    "🔍 Preview Navigation Extraction", 
                                    id="extract-nav-btn", 
                                    color="primary", 
                                    className="w-100 mb-2"
                                )
                            ], width=4),
                            dbc.Col([
                                dbc.Button(
                                    "📝 Edit Extraction", 
                                    id="edit-nav-btn", 
                                    color="secondary", 
                                    className="w-100 mb-2",
                                    disabled=True
                                )
                            ], width=4),
                            dbc.Col([
                                dbc.Button(
                                    "💾 Export llms.txt", 
                                    id="download-nav-btn", 
                                    color="success", 
                                    className="w-100 mb-2",
                                    disabled=True
                                )
                            ], width=4)
                        ])
                    ]),
                    
                    # Preview Area
                    dbc.Row([
                        dbc.Col([
                            dcc.Textarea(
                                id="nav-output",
                                placeholder="Navigation preview will appear here...",
                                style={
                                    "width": "100%", 
                                    "height": "300px",
                                    "fontFamily": "monospace"
                                },
                                readOnly=True
                            )
                        ])
                    ]),
                    
                    # Download Component
                    dcc.Download(id="download-nav")
                ])
            ])
        ]),
        
        # Link Conversion Tab
        dbc.Tab(label="2. Convert Links (to llms.txt format)", tab_id="convert-tab", children=[
            dbc.Card([
                dbc.CardBody([
                    dbc.Row([
                        dbc.Col([
                            dbc.Label("Input Links"),
                            dcc.Textarea(
                                id="input-links",
                                placeholder="Paste Markdown, HTML, or raw URLs...",
                                style={"width": "100%", "height": "200px"}
                            )
                        ])
                    ]),
                    dbc.Row([
                        dbc.Col([
                            dbc.Button(
                                "🔄 Convert Links", 
                                id="convert-links-btn", 
                                color="primary", 
                                className="w-100 mt-2"
                            )
                        ])
                    ]),
                    dbc.Row([
                        dbc.Col([
                            dcc.Textarea(
                                id="converted-links",
                                placeholder="Converted links will appear here...",
                                style={
                                    "width": "100%", 
                                    "height": "200px",
                                    "fontFamily": "monospace"
                                },
                                readOnly=True
                            )
                        ])
                    ])
                ])
            ])
        ]),
        # URL to Markdown Tab
        dbc.Tab(label="3. Convert URL's to Markdown (.md file types)", tab_id="url-convert-tab", children=[
            dbc.Card([
                dbc.CardBody([
                    dbc.Row([
                        dbc.Col([
                            dbc.Label("Input URLs (one per line)"),
                            dcc.Textarea(
                                id="input-urls",
                                placeholder="Paste URLs from llms.txt or enter manually...",
                                style={"width": "100%", "height": "200px"}
                            )
                        ])
                    ]),
                    dbc.Row([
                        dbc.Col([
                            dbc.Button(
                                "🔄 Convert URLs", 
                                id="convert-urls-btn", 
                                color="primary", 
                                className="w-100 mt-2"
                            )
                        ])
                    ]),
                    dbc.Row([
                        dbc.Col([
                            dcc.Textarea(
                                id="converted-urls",
                                placeholder="Converted markdown will appear here...",
                                style={
                                    "width": "100%", 
                                    "height": "300px",
                                    "fontFamily": "monospace"
                                },
                                readOnly=True
                            )
                        ])
                    ]),
                    dbc.Row([
                        dbc.Col([
                            dbc.Button(
                                "💾 Download Markdown Files", 
                                id="download-md-btn", 
                                color="success", 
                                className="w-100 mt-2",
                                disabled=True
                            )
                        ])
                    ]),
                    dcc.Download(id="download-md")
                ])
            ])
        ])
    ])
], fluid=True)

# -----------------------------
# Callbacks
# -----------------------------
@app.callback(
    [Output("nav-output", "value"),
     Output("nav-output", "readOnly"),
     Output("edit-nav-btn", "children"),
     Output("extract-nav-btn", "disabled"),
     Output("edit-nav-btn", "disabled"),
     Output("download-nav-btn", "disabled")],
    [Input("extract-nav-btn", "n_clicks"),
     Input("edit-nav-btn", "n_clicks")],
    [State("homepage-url", "value"),
     State("age-gate-selector", "value"),
     State("cookie-selector", "value"),
     State("root-nav-selector", "value"),
     State("context-selector", "value"),
     State("nav-output", "readOnly"),
     State("nav-output", "value")],
    prevent_initial_call=True
)
def handle_nav_actions(extract_clicks, edit_clicks, homepage_url, age_gate_sel, 
                      cookie_sel, root_nav_selector, context_sel, current_readonly, current_value):
    ctx = dash.callback_context
    
    if not ctx.triggered:
        raise dash.exceptions.PreventUpdate
    
    triggered_id = ctx.triggered[0]['prop_id'].split('.')[0]
    
    if triggered_id == "extract-nav-btn":
        if not homepage_url or not root_nav_selector:
            return ("Error: Provide homepage URL and root navigation selector", 
                    True, "📝 Edit Preview", True, True, True)
        
        try:
            tree = asyncio.run(
                extract_nav_async(homepage_url, age_gate_sel, cookie_sel, root_nav_selector, context_sel)
            )
            
            if not tree:
                return ("No navigation structure found. Try different selectors.", 
                        True, "📝 Edit Preview", True, True, True)
            
            md_tree = format_tree_md(tree, homepage_url)
            homepage_title, homepage_meta = get_homepage_info(homepage_url)
            
            md_lines = [
                f"# {homepage_title}",
                "",
                f"> {homepage_meta}",
                "",
                "## Navigation",
                "",
                md_tree
            ]
            
            llms_md = "\n".join(md_lines)
            return (llms_md, True, "📝 Edit Preview", False, False, False)
        
        except Exception as e:
            return (f"Error during extraction: {str(e)}", 
                    True, "📝 Edit Preview", True, True, True)
    
    elif triggered_id == "edit-nav-btn":
        if edit_clicks % 2 == 1:
            return (current_value, False, "💾 Save Preview", 
                    dash.no_update, dash.no_update, dash.no_update)
        else:
            return (current_value, True, "📝 Edit Preview", 
                    dash.no_update, dash.no_update, dash.no_update)
    
    raise dash.exceptions.PreventUpdate

@app.callback(
    [Output("homepage-url", "value", allow_duplicate=True),
     Output("root-nav-selector", "value", allow_duplicate=True),
     Output("age-gate-selector", "value", allow_duplicate=True),
     Output("cookie-selector", "value", allow_duplicate=True),
     Output("context-selector", "value", allow_duplicate=True)],
    Input("load-lego-example", "n_clicks"),
    prevent_initial_call=True
)
def load_lego_example(n_clicks):
    if n_clicks:
        return set_lego_defaults(None)
    raise dash.exceptions.PreventUpdate

@app.callback(
    [Output("converted-links", "value")],
    [Input("convert-links-btn", "n_clicks")],
    [State("input-links", "value")],
    prevent_initial_call=True
)
def convert_links_callback(n_clicks, input_text):
    if not input_text:
        return ["No input provided"]
    
    converted = convert_links_to_structured(input_text)
    output_lines = [f"- [{text}]({url})" for url, text in converted.items()]
    return ["\n".join(output_lines)]

@app.callback(
    Output("download-nav", "data"),
    Input("download-nav-btn", "n_clicks"),
    State("nav-output", "value"),
    prevent_initial_call=True
)
def download_nav_file(n_clicks, content):
    if content and "Error" not in content:
        return dict(content=content, filename="llms.txt")
    return None

@app.callback(
    [Output("homepage-url", "value"),
     Output("root-nav-selector", "value"),
     Output("age-gate-selector", "value"),
     Output("cookie-selector", "value"),
     Output("context-selector", "value")],
    Input("homepage-url", "id"),
    prevent_initial_call=True
)
def set_lego_defaults(input_id):
    """Pre-populate all fields with working LEGO.com selectors"""
    return (
        "https://www.lego.com/en-ie",  # URL
        '[data-test="main-navigation"]',  # Main nav container
        '[data-test="age-gate-accept"]',  # Age gate accept button
        '[data-test="cookie-accept"]',  # Cookie accept button
        'a[href]:not([href^="#"]), [data-test^="menu"]'  # Context selector
    )

# Update the URL to Markdown Conversion Callback
@app.callback(
    [Output("converted-urls", "value"),
     Output("download-md-btn", "disabled")],
    Input("convert-urls-btn", "n_clicks"),
    State("input-urls", "value"),
    prevent_initial_call=True
)
def convert_urls_to_markdown(n_clicks, input_urls):
    if not input_urls:
        return "No URLs provided", True
    
    # Process URLs, handling both markdown and plain text input
    urls = []
    for line in input_urls.split('\n'):
        line = line.strip()
        # Extract URL from markdown or use line directly
        match = re.search(r'\[.*?\]\((.*?)\)', line)
        if match:
            urls.append(match.group(1))
        elif line.startswith(('http://', 'https://')):
            urls.append(line)
    
    # Track files for download
    processed_files = {}
    
    # Convert URLs to markdown
    converted_content = []
    for url in urls:
        filename, md_content = process_webpage_to_markdown(url)
        processed_files[filename] = md_content
        converted_content.append(f"File: {filename}\n{md_content}\n---\n")
    
    # Store processed files for download
    app.server.processed_markdown_files = processed_files
    
    return "\n".join(converted_content), False


@app.callback(
    Output("download-md", "data"),
    Input("download-md-btn", "n_clicks"),
    prevent_initial_call=True
)
def download_md_files(n_clicks):
    # Retrieve processed files
    processed_files = getattr(app.server, 'processed_markdown_files', {})
    if not processed_files:
        return None
    
    # If only one file, return single file (assume text)
    if len(processed_files) == 1:
        filename, content = next(iter(processed_files.items()))
        return dict(content=content, filename=filename)
    
    # Multiple files - create a zip
    import io, zipfile
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for filename, content in processed_files.items():
            zip_file.writestr(filename, content)
    zip_buffer.seek(0)
    # Encode zip bytes to base64 string
    encoded_zip = base64.b64encode(zip_buffer.read()).decode('utf-8')
    return dict(content=encoded_zip, filename="webpage_markdown_files.zip", base64=True)

if __name__ == "__main__":
    app.run(debug=True)

