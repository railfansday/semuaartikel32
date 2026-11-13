import os
import json
import sqlite3
import feedparser
import requests
import random
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

# --- KONFIGURASI PATH & DOMAIN ---
DB_PATH = "database.sqlite"
RSS_FILE = "rss_list.txt"
OUTPUT_DIR = "."
BASE_URL = "https://thenewsday112.github.io/semuaartikel5"

# Header HTTP agar scraping tidak diblokir
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

# --------------------------------------------------------------------------
# FUNGSI PARAFFRASE LOKAL PYTHON (Tanpa AI / Tanpa API Key)
# --------------------------------------------------------------------------
def local_paraphrase(text_html):
    if not text_html:
        return text_html
    
    try:
        soup = BeautifulSoup(text_html, 'html.parser')
        paragraphs = soup.find_all('p')
        
        new_paragraphs = []
        for p in paragraphs:
            text = p.get_text().strip()
            if not text:
                continue
                
            # Pecah kalimat berdasarkan titik
            sentences = [s.strip() for s in text.split('.') if s.strip()]
            
            # Jika kalimat lebih dari 1, kita lakukan teknik parafrase lokal:
            # Mengubah urutan atau menyisipkan variasi transisi agar unik
            if len(sentences) > 1:
                # Contoh variasi pergeseran posisi atau penambahan frasa natural
                transition_phrases = [
                    "Berdasarkan laporan, ", 
                    "Sebagaimana diketahui, ", 
                    "Dalam perkembangan terbarunya, ", 
                    "Diungkapkan bahwa "
                ]
                # Modifikasi kalimat pertama dengan transisi secara acak
                if not sentences[0].startswith(tuple(transition_phrases)):
                    sentences[0] = random.choice(transition_phrases) + sentences[0].lower()
                
                # Jika ada 3 kalimat atau lebih, balik urutan kalimat tengah untuk keunikan struktur
                if len(sentences) >= 3:
                    middle = sentences[1:-1]
                    random.shuffle(middle)
                    sentences[1:-1] = middle
            
            modified_text = '. '.join(sentences)
            if not modified_text.endswith('.'):
                modified_text += '.'
                
            new_paragraphs.append(f"<p>{modified_text}</p>")
            
        if not new_paragraphs:
            return text_html
            
        return "\n".join(new_paragraphs)
    except Exception:
        return text_html

# --------------------------------------------------------------------------
# KODE IKLAN ADSTERRA
# --------------------------------------------------------------------------
ADSTERRA_CODE = """
<div style="text-align:center; margin:20px 0;">
    <script type="text/javascript">
        atOptions = {
            'key' : 'f9e40eb3b3cd05f1038aac7b36b33ad2',
            'format' : 'iframe',
            'height' : 90,
            'width' : 728,
            'params' : {}
        };
    </script>
    <script type="text/javascript" src="https://upholdbrilliancerecast.com/f9e40eb3b3cd05f1038aac7b36b33ad2/invoke.js"></script>
</div>
"""

# --------------------------------------------------------------------------
# INISIALISASI DATABASE
# --------------------------------------------------------------------------
def init_db():
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS artikel (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            link TEXT UNIQUE,
            judul TEXT,
            konten TEXT,
            gambar TEXT,
            tanggal_publish TEXT,
            tanggal_update TEXT
        )
    """)
    
    cursor.execute("PRAGMA table_info(artikel)")
    columns = [col[1] for col in cursor.fetchall()]
    
    if 'gambar' not in columns:
        cursor.execute("ALTER TABLE artikel ADD COLUMN gambar TEXT")
    if 'tanggal_publish' not in columns:
        cursor.execute("ALTER TABLE artikel ADD COLUMN tanggal_publish TEXT")
    if 'tanggal_update' not in columns:
        cursor.execute("ALTER TABLE artikel ADD COLUMN tanggal_update TEXT")
    
    conn.commit()
    conn.close()

# --------------------------------------------------------------------------
# EXTRACT KONTEN + GAMBAR + TANGGAL
# --------------------------------------------------------------------------
def extract_full_content(url):
    try:
        response = requests.get(url, headers=HEADERS, timeout=8)
        if response.status_code != 200:
            return None, None, None
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        image_url = None
        og_image = soup.find('meta', property='og:image') or soup.find('meta', attrs={'name': 'og:image'})
        if og_image and og_image.get('content'):
            image_url = og_image['content']
        
        tanggal = None
        for meta_prop in ['article:published_time', 'article:modified_time', 'datePublished']:
            meta = soup.find('meta', property=meta_prop) or soup.find('meta', attrs={'name': meta_prop})
            if meta and meta.get('content'):
                tanggal = meta['content']
                break
        
        paragraphs = soup.find_all('p')
        full_text = []
        for p in paragraphs:
            text = p.get_text().strip()
            if len(text) > 40 and not any(x in text.lower() for x in ['cookie', 'privacy policy', 'subscribe', 'all rights reserved', 'terms of use']):
                full_text.append(f"<p>{text}</p>")
                
        if not full_text:
            return None, image_url, tanggal
            
        return "\n".join(full_text), image_url, tanggal
    except Exception:
        return None, None, None

# --------------------------------------------------------------------------
# PROCESS SINGLE FEED
# --------------------------------------------------------------------------
def process_single_feed(feed_url):
    feed_url = feed_url.strip()
    if not feed_url or feed_url.startswith("#"):
        return 0, 0

    baru = 0
    duplikat = 0
    
    try:
        feed = feedparser.parse(feed_url)
        conn = sqlite3.connect(DB_PATH, timeout=15)
        cursor = conn.cursor()
        
        for entry in feed.entries:
            link = entry.get('link', '')
            judul = entry.get('title', 'Tanpa Judul')
            
            if not link:
                continue

            cursor.execute("SELECT id FROM artikel WHERE link = ?", (link,))
            if cursor.fetchone():
                duplikat += 1
                continue

            full_text_html, image_url, tanggal = extract_full_content(link)
            
            if not full_text_html:
                summary = entry.get('summary', entry.get('description', 'Konten tidak tersedia.'))
                full_text_html = f"<p>{summary}</p>"
            
            # --- JALANKAN PARAFFRASE LOKAL PYTHON ---
            print(f"[*] Memproses & memparafrase lokal: {judul[:40]}...")
            full_text_html = local_paraphrase(full_text_html)

            if not tanggal:
                published_parsed = entry.get('published_parsed')
                if published_parsed:
                    tanggal = datetime(*published_parsed[:6]).isoformat()
                else:
                    tanggal = datetime.now().isoformat()
            
            tanggal_update = datetime.now().isoformat()
            
            img_tag = f'<div style="text-align:center; margin-bottom:20px;"><img src="{image_url}" alt="{judul}" style="max-width:100%; height:auto; border-radius:8px;" loading="lazy"></div>' if image_url else ""
            
            konten_lengkap = f"""
            {img_tag}
            {ADSTERRA_CODE}
            <div class="article-body" style="font-size: 16px; line-height: 1.8; color: #333;">
                {full_text_html}
                <p style="margin-top: 25px;">
                    <a href="{link}" target="_blank" rel="nofollow noopener" style="color: #0d6efd; text-decoration: none; font-weight: bold;">
                        &rarr; Baca artikel asli
                    </a>
                </p>
            </div>
            {ADSTERRA_CODE}
            """
            
            try:
                cursor.execute(
                    "INSERT INTO artikel (link, judul, konten, gambar, tanggal_publish, tanggal_update) VALUES (?, ?, ?, ?, ?, ?)",
                    (link, judul, konten_lengkap, image_url, tanggal, tanggal_update)
                )
                conn.commit()
                baru += 1
            except sqlite3.IntegrityError:
                duplikat += 1
            except Exception:
                pass
                
        conn.close()
    except Exception:
        pass
        
    return baru, duplikat

# --------------------------------------------------------------------------
# GENERATE SITEMAP.XML & ROBOTS.TXT
# --------------------------------------------------------------------------
def generate_sitemap_and_robots():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id, tanggal_update FROM artikel ORDER BY id DESC")
    articles = cursor.fetchall()
    conn.close()
    
    today = datetime.now().strftime('%Y-%m-%d')
    
    sitemap_path = os.path.join(OUTPUT_DIR, "sitemap.xml")
    xml_parts = ['<?xml version="1.0" encoding="UTF-8"?>']
    xml_parts.append('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">')
    xml_parts.append(f'<url><loc>{BASE_URL}/</loc><lastmod>{today}</lastmod><changefreq>daily</changefreq><priority>1.0</priority></url>')
    xml_parts.append(f'<url><loc>{BASE_URL}/page2.html</loc><lastmod>{today}</lastmod><changefreq>daily</changefreq><priority>0.9</priority></url>')
    
    for artikel_id, tanggal_update in articles:
        lastmod = tanggal_update[:10] if tanggal_update else today
        xml_parts.append(f'<url><loc>{BASE_URL}/index.html#artikel-{artikel_id}</loc><lastmod>{lastmod}</lastmod><changefreq>weekly</changefreq><priority>0.7</priority></url>')
    xml_parts.append('</urlset>')
    
    with open(sitemap_path, "w", encoding="utf-8") as f:
        f.write("\n".join(xml_parts))
    
    robots_path = os.path.join(OUTPUT_DIR, "robots.txt")
    robots_content = f"User-agent: *\nAllow: /\nSitemap: {BASE_URL}/sitemap.xml\nCrawl-delay: 1\n"
    with open(robots_path, "w", encoding="utf-8") as f:
        f.write(robots_content)

# --------------------------------------------------------------------------
# EXPORT KE INDEX.HTML & PAGE2.HTML
# --------------------------------------------------------------------------
def export_to_two_files():
    if not os.path.exists(DB_PATH):
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id, judul, konten, gambar, tanggal_publish, tanggal_update, link FROM artikel ORDER BY id ASC")
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return

    total_articles = len(rows)
    mid_point = (total_articles + 1) // 2

    parts = [
        ("index.html", rows[:mid_point], "Halaman Utama (Update Terkini)", "page2.html", "Halaman 1"),
        ("page2.html", rows[mid_point:], "Halaman 2 (Arsip Berita)", "index.html", "Halaman 2")
    ]

    for filename, chunk, part_name, other_part, page_label in parts:
        output_file = os.path.join(OUTPUT_DIR, filename)
        canonical_url = f"{BASE_URL}/{filename}"
        sample_titles = ", ".join([row[1][:50] for row in chunk[:5]])
        meta_desc = f"Katalog berita interaktif: {sample_titles}. Update harian dengan informasi terpercaya."[:160]
        
        html_parts = []
        html_parts.append(f"""<!DOCTYPE html>
<html lang="id">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Portal Berita Interaktif - {part_name}</title>
    <meta name="description" content="{meta_desc}">
    <link rel="canonical" href="{canonical_url}">
    <script src="https://cdnjs.cloudflare.com/ajax/libs/html2pdf.js/0.10.1/html2pdf.bundle.min.js"></script>
    <style>
        :root {{
            --primary: #2563eb;
            --primary-dark: #1d4ed8;
            --bg-color: #f8fafc;
            --card-bg: #ffffff;
            --text-main: #1e293b;
            --text-muted: #64748b;
            --border: #e2e8f0;
        }}
        body {{
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background-color: var(--bg-color);
            color: var(--text-main);
            margin: 0;
            padding: 20px;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
        }}
        .header-box {{
            background: var(--card-bg);
            padding: 30px;
            border-radius: 12px;
            box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1);
            text-align: center;
            margin-bottom: 30px;
        }}
        .header-box h1 {{
            margin: 0 0 10px 0;
            font-size: 28px;
            color: var(--text-main);
        }}
        .search-box {{
            margin-top: 20px;
            max-width: 500px;
            margin-left: auto;
            margin-right: auto;
        }}
        .search-box input {{
            width: 100%;
            padding: 12px 20px;
            border: 2px solid var(--border);
            border-radius: 8px;
            font-size: 16px;
            outline: none;
            transition: border-color 0.2s;
        }}
        .search-box input:focus {{
            border-color: var(--primary);
        }}
        .grid-container {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(350px, 1fr));
            gap: 25px;
            margin-bottom: 40px;
        }}
        .article-card {{
            background: var(--card-bg);
            border-radius: 12px;
            box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05);
            padding: 20px;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
            transition: transform 0.2s, box-shadow 0.2s;
            position: relative;
        }}
        .article-card:hover {{
            transform: translateY(-4px);
            box-shadow: 0 10px 15px -3px rgba(0,0,0,0.1);
        }}
        .article-card img {{
            width: 100%;
            height: 180px;
            object-fit: cover;
            border-radius: 8px;
            margin-bottom: 15px;
        }}
        .article-card h2 {{
            font-size: 18px;
            margin: 0 0 10px 0;
            color: var(--text-main);
            line-height: 1.4;
        }}
        .article-meta {{
            font-size: 13px;
            color: var(--text-muted);
            margin-bottom: 15px;
        }}
        .card-actions {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-top: 1px solid var(--border);
            padding-top: 15px;
            margin-top: auto;
        }}
        .btn {{
            background-color: var(--primary);
            color: white;
            border: none;
            padding: 8px 14px;
            border-radius: 6px;
            cursor: pointer;
            font-size: 13px;
            font-weight: 600;
            text-decoration: none;
            transition: background 0.2s;
        }}
        .btn:hover {{
            background-color: var(--primary-dark);
        }}
        .btn-pdf {{
            background-color: #ef4444;
        }}
        .btn-pdf:hover {{
            background-color: #dc2626;
        }}
        .pagination {{
            text-align: center;
            margin: 40px 0;
        }}
        .pagination a {{
            display: inline-block;
            padding: 10px 20px;
            margin: 0 5px;
            background: white;
            border-radius: 6px;
            text-decoration: none;
            color: var(--text-main);
            font-weight: bold;
            box-shadow: 0 2px 4px rgba(0,0,0,0.05);
        }}
        .pagination a.active {{
            background: var(--primary);
            color: white;
        }}
        .modal {{
            display: none;
            position: fixed;
            z-index: 1000;
            left: 0;
            top: 0;
            width: 100%;
            height: 100%;
            background-color: rgba(0,0,0,0.5);
            justify-content: center;
            align-items: center;
            padding: 20px;
        }}
        .modal-content {{
            background: white;
            width: 100%;
            max-width: 800px;
            max-height: 85vh;
            border-radius: 12px;
            padding: 30px;
            overflow-y: auto;
            position: relative;
        }}
        .close-modal {{
            position: absolute;
            top: 15px;
            right: 20px;
            font-size: 24px;
            cursor: pointer;
            color: var(--text-muted);
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header-box">
            <h1>Katalog Berita & Artikel Interaktif - {part_name}</h1>
            <p>Total {total_articles} artikel tersedia dalam direktori berita harian.</p>
            <div class="search-box">
                <input type="text" id="searchInput" placeholder="Cari judul artikel..." onkeyup="filterArticles()">
            </div>
            <div style="margin-top: 15px;">
                <a href="{other_part}" style="color: var(--primary); text-decoration: none; font-weight: bold;">📄 Beralih ke {("Halaman 2" if filename == "index.html" else "Halaman Utama")} →</a>
            </div>
        </div>

        <div class="grid-container" id="articleGrid">""")

        for item in chunk:
            artikel_id
