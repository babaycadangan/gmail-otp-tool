import os
import re
import imaplib
import email
import json
from email.header import decode_header
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify
import secrets

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)

# ========== KONFIGURASI GMAIL ==========
# Ambil dari environment variables
GMAIL_USER = os.environ.get('GMAIL_USER', '')
GMAIL_APP_PASS = os.environ.get('GMAIL_APP_PASS', '')
# ========================================

def decode_mime_header(header_value):
    """Decode MIME encoded header biar tidak ada karakter aneh."""
    if not header_value:
        return ''
    decoded_parts = decode_header(header_value)
    result = []
    for part, charset in decoded_parts:
        if isinstance(part, bytes):
            try:
                charset = charset or 'utf-8'
                result.append(part.decode(charset, errors='replace'))
            except:
                result.append(part.decode('utf-8', errors='replace'))
        else:
            result.append(str(part))
    return ' '.join(result)

def extract_otps(text):
    """Ekstrak kode OTP dari teks."""
    # Pola-pola yang umum untuk kode OTP
    patterns = [
        r'(?:OTP|Kode|Code|Verification|PIN|kode|verifikasi|otp)\s*[:.\-]?\s*(\d{4,8})',
        r'(?:is|your|Your|adalah)\s*(?::)?\s*(\d{4,8})',
        r'(?<!\d)(\d{4,8})(?!\d)',
        r'(?:OTP|Code)\s*[:.\-]?\s*([A-Za-z0-9]{4,10})',
    ]
    
    otps = []
    for pattern in patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        for match in matches:
            if isinstance(match, tuple):
                otp = ''.join(match).strip()
            else:
                otp = match.strip()
            if otp and len(otp) >= 4 and len(otp) <= 10 and otp not in otps:
                otps.append(otp)
    return otps

def fetch_otp_emails(hours_back=24, sender_filter=None, keyword_filter=None):
    """Ambil email dari Gmail via IMAP dan cari OTP."""
    if not GMAIL_USER or not GMAIL_APP_PASS:
        return {'error': 'GMAIL_USER atau GMAIL_APP_PASS belum diatur'}
    
    try:
        print("Menghubungkan ke Gmail IMAP...")
        mail = imaplib.IMAP4_SSL('imap.gmail.com')
        mail.login(GMAIL_USER, GMAIL_APP_PASS)
        mail.select('INBOX')
        
        # Hitung tanggal mundur
        since_date = (datetime.now() - timedelta(hours=hours_back)).strftime('%d-%b-%Y')
        
        # Bangun query
        search_parts = [f'(SINCE {since_date})']
        
        # Filter pengirim
        if sender_filter:
            search_parts.append(f'(FROM "{sender_filter}")')
        
        # Filter kata kunci
        if keyword_filter:
            search_parts.append(f'(SUBJECT "{keyword_filter}")')
        else:
            search_parts.append('(OR SUBJECT "OTP" SUBJECT "verification" SUBJECT "kode" SUBJECT "security code")')
        
        search_query = b' '.join([p.encode() for p in search_parts])
        print(f"Mencari email dengan query...")
        
        status, message_ids = mail.search(None, search_query)
        
        if status != 'OK' or not message_ids[0]:
            mail.logout()
            return {'success': True, 'count': 0, 'results': []}
        
        # Ambil ID email, balik urutan (terbaru dulu), batasi 100
        ids = message_ids[0].split()
        ids = ids[::-1][:100]
        
        print(f"Ditemukan {len(ids)} email, memproses...")
        
        results = []
        for msg_id in ids:
            status, msg_data = mail.fetch(msg_id, '(RFC822)')
            if status != 'OK':
                continue
            
            raw_email = msg_data[0][1]
            msg = email.message_from_bytes(raw_email)
            
            subject = decode_mime_header(msg['Subject'])
            sender = decode_mime_header(msg['From'])
            date_str = msg['Date'] or ''
            
            # Ambil body email
            body = ''
            if msg.is_multipart():
                for part in msg.walk():
                    if 'attachment' in str(part.get('Content-Disposition', '')):
                        continue
                    payload = part.get_payload(decode=True)
                    if payload:
                        try:
                            charset = part.get_content_charset() or 'utf-8'
                            decoded = payload.decode(charset, errors='replace')
                            if part.get_content_type() == 'text/html':
                                decoded = re.sub(r'<[^>]+>', ' ', decoded)
                                decoded = re.sub(r'\s+', ' ', decoded)
                            body += decoded
                        except:
                            body += payload.decode('utf-8', errors='replace')
            else:
                payload = msg.get_payload(decode=True)
                if payload:
                    try:
                        charset = msg.get_content_charset() or 'utf-8'
                        body += payload.decode(charset, errors='replace')
                    except:
                        body += payload.decode('utf-8', errors='replace')
            
            otps = extract_otps(body)
            
            if otps:
                results.append({
                    'id': msg_id.decode(),
                    'subject': subject,
                    'sender': sender,
                    'date': date_str,
                    'otps': otps,
                    'preview': body[:200].replace('\n', ' ').strip()[:200]
                })
        
        mail.logout()
        print(f"Selesai! Ditemukan {len(results)} email mengandung OTP")
        return {'success': True, 'count': len(results), 'results': results}
        
    except imaplib.IMAP4.error as e:
        error_msg = str(e)
        if 'LOGIN failed' in error_msg:
            return {'error': 'Gagal login. Periksa email dan App Password!'}
        return {'error': f'Error IMAP: {error_msg}'}
    except Exception as e:
        return {'error': f'Error: {str(e)}'}

@app.route('/')
def index():
    status = "terkonfigurasi" if (GMAIL_USER and GMAIL_APP_PASS) else "belum dikonfigurasi"
    return render_template('index.html', config_status=status)

@app.route('/api/status')
def api_status():
    return jsonify({
        'configured': bool(GMAIL_USER and GMAIL_APP_PASS)
    })

@app.route('/api/fetch', methods=['POST'])
def api_fetch():
    data = request.get_json() or {}
    hours_back = int(data.get('hours_back', 24))
    sender_filter = data.get('sender_filter', '').strip()
    keyword_filter = data.get('keyword_filter', '').strip()
    
    result = fetch_otp_emails(
        hours_back=hours_back,
        sender_filter=sender_filter if sender_filter else None,
        keyword_filter=keyword_filter if keyword_filter else None
    )
    
    if 'error' in result:
        return jsonify(result), 400
    return jsonify(result)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)