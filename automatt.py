#!/usr/bin/env python

import csv
import email
import functools
import os
import random
import re
import sys
import textwrap
import time
import urllib

import discord
import feedparser
import gspread
import puz
import requests
import yagmail
import yaml
import xmltodict

import xword_dl

from bs4 import BeautifulSoup
from imapclient import IMAPClient
from requests.adapters import HTTPAdapter
from titlecase import titlecase

from datetime import datetime, timedelta
from urllib3.util import Retry
from zipfile import ZipFile, is_zipfile

requests.get = functools.partial(requests.get, headers={'User-Agent':'Automatt'}, timeout=10)
requests.head = functools.partial(requests.head, headers={'User-Agent':'Automatt'}, timeout=10)

def create_html_list(records):
    indent = "    "

    html_list = textwrap.dedent("""\
            <!DOCTYPE html>
            <html lang="en">
                <head>
                    <meta charset="utf-8" />
                    <title>Automatt Output</title>
                    <style>
                        h1, p {{
                            margin-left: 40px;
                        }}
                        a {{
                            color: #f90;
                            text-decoration: none;
                        }}
                        a:hover {{
                            text-decoration: underline;
                        }}
                        body {{
                            width: 750px;
                        }}
                        .unfetched:before, .fetched:before {{
                            display: inline-block;
                            width: 30px;
                            margin-left: -30px;
                        }}
                        .unfetched:before {{
                            content: '❌';
                        }}
                        .fetched:before {{
                            content: '✔️';
                        }}
                    </style>
                </head>
                <body>
                    <h1>{}</h1>
                    <p>\n""".format(datetime.today().strftime('%A, %B %-d, %Y')))

    for rec in records:
        template = rec.get('template') or ''

        rec['formatted'] = format_string(template, rec)

        if rec.get('puzfile'):
            cls = 'fetched'
        else:
            cls = 'unfetched'

        if rec['formatted']:

            html_list += 3 * indent + '<span class="{}">'.format(cls)
            html_list += "{}</span><br />\n".format(rec.get('formatted'))

        elif not any(rec[key] for key in rec.keys()):
            html_list += 2 * indent + "</p>\n" + 2 * indent + "<p>\n"

    html_list += 2 * indent + "</p>\n"

    return html_list

def create_html_postscript(html):
    indent = "    "
    html = 2 * indent + '<p><em>' + html + '</em></p>\n'
    return html

def create_html_blocklist(entries, title=None):
    indent = "    "
    html = ''
    if title:
        html += 2 * indent + "<p><strong>" + title + "</strong></p>\n\n"

    html += 2 * indent + '<p>'

    html_list = []
    for entry in entries:
        e = ''
        if entry.get('Name') and entry.get('Link'):
            e += '<a href="{}">{}</a>'.format(entry.get('Link'), 
                                              entry.get('Name'))
        elif entry.get('Name'):
            e += entry.get('Name')

        if entry.get('Comment'):
            e += ' ' + entry.get('Comment')
        html_list.append(e)

    html += ' | '.join(html_list) + '</p>\n\n'

    return html

def get_possible_puzfiles(url):
    headers = {'User-Agent': 'Automatt'}
    res = requests.get(url, headers=headers)
    soup = BeautifulSoup(res.text, 'html.parser')
        
    possible_puzfiles = [a.get('href', '') for a in soup.find_all('a') 
                         if a.get('href','') and
                         ('.puz' in a.get('href', '').lower()
                          or '.jpz' in a.get('href','').lower()
                          or any(s in a.get_text().lower() for 
                                 s in ['.puz', 'acrosslite', 'across lite', 
                                       'puz file', 'jpz'])
                          or a.get_text().lower() == 'puz')
                         and 'litsoft.com' not in a.get('href')]

    possible_puzfiles = [urllib.parse.urljoin(url, link) for link in
                         possible_puzfiles if link]

    if 'crosshare.org/crosswords' in url:
        url_components = url.split('/')
        puzzle_id = url_components[url_components.index('crosswords') + 1]
        possible_puzfiles.insert(0, 'https://crosshare.org/api/puz/{}'.format(puzzle_id))

    for iframe in soup.find_all('iframe'):
        src = iframe.get('src', '')
        if 'crosshare.org/embed' in src:
            src_components = src.split('/')
            puzzle_id = src_components[src_components.index('embed') + 1]
            possible_puzfiles.insert(0, 'https://crosshare.org/api/puz/{}'.format(puzzle_id))

    return possible_puzfiles

def send_to_discord(msg, attachment, token, channel_id):
    intents = discord.Intents.default()
    client = discord.Client(intents=intents)

    print('sending to discord')

    @client.event
    async def on_ready():
        channel = client.get_channel(channel_id)
        if attachment:
            filename = discord.File(attachment)
            await channel.send(msg, file=filename)
        else:
            await channel.send(msg)
        await client.close()

    client.run(token)

def send_to_wordpress(draft_post, tags, token):
    wp_api_url = 'https://public-api.wordpress.com/rest/v1.1/sites/dailycrosswordlinks.com/posts/new?context=edit'
    post_data = {
        'title': datetime.today().strftime('%A, %B %-d, %Y'),
        'content': draft_post,
        'tags': ','.join(tags),
        'status': 'draft',
        }
    wp_headers = {
        'Authorization': 'Bearer ' + token,
    }

    requests.post(wp_api_url, data=post_data, headers=wp_headers)

def handle_inbox_check(site, mailserver):
    records = []

    site_from_address = site.get('Email address')

    yesterday = datetime.today() - timedelta(days=1)

    msg_ids = mailserver.search(['FROM', site_from_address, 'SINCE', yesterday])

    for msg_id, data in mailserver.fetch(msg_ids, 'RFC822').items():
        record = {}
        record['name'] = site.get('Name', '')

        msg = email.message_from_bytes(data[b'RFC822'])
        record['pagetitle'] = msg.get('Subject')

        for attachment in msg.get_payload():
            filename = attachment.get_filename()
            if filename and (filename.lower().endswith('.puz') or filename.lower().endswith('.jpz')):
                print('saving puzzle as', filename)
                open(filename, 'wb').write(attachment.get_payload(decode=True))
                record['puzfile'] = filename
                break

        records.append(record)

    return records


def handle_rss_feed(site):
    records = []

    site_url = site.get('RSS')

    cache_buster = '&' if '?' in site.get('RSS') else '?'
    cache_buster += str(random.randint(100,999))

    retries = Retry(total=10, backoff_factor=0.2)

    with requests.Session() as s:
        s.mount('http', HTTPAdapter(max_retries=retries))
        s.headers.update({'User-Agent': 'Automatt / Daily Crossword Links bot'})
        res = s.get(site.get('RSS') + cache_buster)
        res.raise_for_status()

    f = feedparser.parse(res.content)

    if f.bozo:
        raise Exception('RSS feed appears to be empty or invalid.')

    new_posts = [entry for entry in f.entries 
                 if entry and time.mktime(time.gmtime()) - 
                 time.mktime(entry.get('published_parsed')) <= (86400 * 1 + 120)]

    for entry in new_posts:
        record = {}
        res = requests.head(entry.get('link'), allow_redirects=True)
        link = res.url.split('&')[0]

        print(entry.get('title','') + ':', link)

        record['name'] = site.get('Name', f.get('feed').get('title',''))
        record['title'] = record['pagetitle'] = entry.get('title','')
        record['link'] = link

        filename = handle_page(link)
 
        if filename:
            record['puzfile'] = filename

        records.append(record)

    return records

def handle_page(link):
    possible_puzfiles = get_possible_puzfiles(link)
 
    filename = ''

    while possible_puzfiles and not filename:
        url = possible_puzfiles.pop(0)
        try:
            filename = handle_direct_download(url).get('puzfile', '')
        except:
            pass

    if not filename:
        print('attempting xword-dl download of', link)
        try:
            puzzle, filename = xword_dl.by_url(link)
            print('Using xword-dl to save puz as {}'.format(filename))
            puzzle.save(filename)
        except:
            print('No puzzle found.')
            filename = ''

    return filename

def handle_xword_download(site):
    record = {}

    argument = site.get('Tech').split(' ')[1]
    
    puzzle, filename = xword_dl.by_keyword(argument)

    puzzle.save(filename)
    record['puzfile'] = filename

    return record

def handle_direct_download(link):
    record = {}

    filename = ''

    headers = {'User-Agent': 'Automatt'}

    if 'drive.google.com/file' in link:
        google_id = link.split('/')[5]
        link = 'https://drive.google.com/uc?export=download&id=' + google_id
    elif 'dropbox.com' in link and not link.endswith('dl=1'):
        link += '&dl=1' if '?' in link else '?dl=1'
    
    res = requests.get(link, headers=headers)
    res.raise_for_status() 

    if link.split('?')[0].endswith('.puz') or link.split('?')[0].endswith('.jpz'):
        filename = link.split('/')[-1].split('?')[0]
        filename = urllib.parse.unquote(filename)
    elif res.headers.get('Content-Disposition', ''):
        cd = res.headers.get('Content-Disposition')
        filename = re.findall('filename=(.+)', 
                              cd)[0].split(';')[0].strip('"')

    if filename.endswith('.puz'):
        try:
            p = puz.load(res.content)
            print('Saving puz as {}'.format(filename))
            p.save(filename)

            record['puzfile'] = filename
        except:
            raise Exception('Apparently malformed puzzle file at', link)
    elif filename.endswith('.jpz'):
        with open(filename, 'wb') as f:
            f.write(res.content)
        record['puzfile'] = filename

    return record

    
def format_string(template, record={}):
    tokens = {
        '%link': record.get('link') or record.get('homepage',''),
        '%homepage': record.get('homepage') or '',
        '%sitename': record.get('name') or '',
        '%pagetitle': record.get('pagetitle') or '%puztitle',
        '%author': record.get('author') or 'tktktk',
        '%puztitle': record.get('title') or 'tktktk',
        '%blank': ''
        }

    supported_date_tokens = ['d','-d','m','-m','y','Y','B']
 
    for t in ['%yest' + dt for dt in supported_date_tokens]:
        yesterday = datetime.today() - timedelta(1)
        tokens[t] = yesterday.strftime(t.replace('yest',''))

    for t in ['%' + dt for dt in supported_date_tokens]:
        tokens[t] = datetime.today().strftime(t)

    for token in tokens:
        template = template.replace(token, tokens[token])

    return template


def check_and_handle(site, mailserver):
    to_check_dow = []
    to_check_dom = []
    records = []
    problem = ''

    for index, weekday in enumerate(['Mon','Tue','Wed','Thu',
                                     'Fri','Sat','Sun']):
        if site.get(weekday):
            to_check_dow.append(index)

    if site.get('DOM'):
        to_check_dom.extend([int(d) for d in str(site.get('DOM')).split(',')])

    dow = datetime.today().weekday()
    dom = datetime.today().day

    if site.get('RSS'):
        try:
            records.extend(handle_rss_feed(site))
        except Exception as e:
            problem += str(e) + '\n'

    if site.get('Email address'):
        try:
            records.extend(handle_inbox_check(site, mailserver))
        except Exception as e:
            problem += str(e) + '\n'

    if not records and (dow in to_check_dow or dom in to_check_dom):
        record = {}
        try:
            if 'xword-dl' in site.get('Tech'):
                record = handle_xword_download(site)

            elif 'direct' in site.get('Tech'):
                link = format_string(site.get('Direct Link', ''))
                record = handle_direct_download(link)

            elif 'page' in site.get('Tech'):
                link = format_string(site.get('Direct Link')) or site.get('Homepage')
                filename = handle_page(link)
                if filename:
                    record = {'puzfile':filename}

        except Exception as e:
            problem += str(e) + '\n'

        records.append(record)

    if problem and not records:
        records = [{'problem':problem}]

    for rec in records:
        rec['name'] = rec.get('name', site.get('Name'))
        rec['homepage'] = site.get('Homepage', '')
        rec['link'] = rec.get('link', site.get('Direct Link', ''))
        
        rec['link'] = format_string(rec['link'], rec)

        if rec.get('puzfile') and rec.get('puzfile').endswith('.puz'):
            p = puz.read(rec.get('puzfile'))
            rec['author'] = p.author
            rec['title'] = p.title or rec.get('title', '')

        elif rec.get('puzfile') and rec.get('puzfile').endswith('.jpz'):
            try:
                if is_zipfile(rec.get('puzfile')):
                    with ZipFile(rec.get('puzfile')) as zf:
                        with zf.open(zf.namelist()[0]) as x:
                            jpz_info = xmltodict.parse(x)
                else:
                    with open(rec.get('puzfile'), 'rb') as f:
                        jpz_info = xmltodict.parse(f)

                main_data = jpz_info.get('crossword-compiler') or jpz_info.get('crossword-compiler-applet')
                metadata = main_data['rectangular-puzzle']['metadata']
                rec['author'] = metadata['creator']
                rec['title'] = metadata['title']

            except Exception as e:
                problem += 'JPZ parsing issue: ' + str(e) + '\n'

        if rec.get('author'):
            rec['author'] = BeautifulSoup(rec['author']).get_text()
            rec['author'] = rec['author'].split('/')[0]
            rec['author'] = rec['author'].split(', edited')[0]
            rec['author'] = rec.get('author', '').strip()
            if any(rec.get('author').startswith(b) 
                    for b in ['by ', 'By ', 'BY ']):
                rec['author'] = rec['author'][3:]

        if rec['name'] in ['Newsday', 'USA Today', 'BEQ', 'New York Times']:
            rec['title'] = titlecase(rec.get('title', ''))

        if any(site.get(tag) for tag in ['Bold', 'Normal']):
            template = ' '.join(['<strong>' + site.get('Bold') + '</strong>',
                                 site.get('Normal')])
        else:
            template = '<strong><a href="%link">%sitename</a>: %puztitle</strong> by %author.'

        template += ' <em>' + (site.get('Italic') or 'tktktk') + '</em>'

        rec['template'] = template

        rec['problem'] = problem

    return records


def main():
    datestring = datetime.today().strftime('%Y%m%d')

    os.chdir(os.path.dirname(__file__) or '.')
    os.makedirs(datestring, exist_ok=True)

    gc = gspread.service_account('gridsmaker-36ebd6ceb309.json')
    sh = gc.open('Puzzle sources')
    google_sheet = sh.sheet1.get_all_records()

    with open('email.yaml') as f:
        config = yaml.safe_load(f)

    from_address = config['from_address']
    from_email = [*from_address][0]
    password = config['password']
    recipients = config['recipients']
    message = config['message']
    subject = config['subject']

    imap_server = config['imap_server']

    mailserver = IMAPClient(imap_server)
    mailserver.login(from_email, password)
    mailserver.select_folder('INBOX')

    os.chdir(datestring)

    daily_records = []
    possible_problems = []

    for site in google_sheet:
        if not any(site[key] for key in site.keys()):
            daily_records.append({})

        try:
            print('checking', site['Name'])
            daily_records.extend(check_and_handle(site, mailserver))
            time.sleep(1)
        except Exception as e:
            print('issue encountered:', str(e))
            possible_problems.append((site['Name'], str(e)))

        if (any('%homepage' in site.get(f) for f in ['Bold', 'Normal','Italic'])
                and not site.get('Homepage')):
            possible_problems.append((site['Name'],
                'No homepage specified: link likely broken'))

    possible_problems.extend([(rec.get('name'), rec.get('problem')) for
        rec in daily_records if rec.get('problem')])
     
    with open('index.html', 'w') as f:
        html_doc = create_html_list(daily_records)
        for graf in sh.worksheet('Post-script').col_values(1):
            html_doc += create_html_postscript(graf)
        html_doc += create_html_blocklist(
                sh.worksheet('Other American').get_all_records(),
                title='Other American-style links:')
        html_doc += create_html_blocklist(
                sh.worksheet('Other Cryptic/Variety').get_all_records(),
                title='Other Cryptic/Variety links:')
        html_doc += """
        </body>
    </html>"""
        f.write(html_doc)

    with open(datestring + '.csv', 'w') as f:
        fields = ['name', 'title', 'author', 'link', 'puzfile', 
                  'formatted', 'problem']
        writer = csv.DictWriter(f, fields, extrasaction='ignore')
        writer.writeheader()
        for row in daily_records:
            writer.writerow(row)

    os.chdir('..')
    with ZipFile(datestring + '.zip', 'w') as zipf:
        for f in os.listdir(datestring):
            zipf.write(datestring + '/' + f, f)
    
    subject = datetime.today().strftime(subject)
    message = message.format(
                entrycount=len([e for e in daily_records if e]),
                puzcount=len([e for e in daily_records if e.get('puzfile')]))

    reminders = sh.worksheet('Reminder').get_all_records()
    to_remind = ''

    for r in reminders:
        try:
            days = [int(d) for d in str(r.get('DOM')).split(',')]
            if datetime.today().day in days:
                to_remind += "- "
                to_remind += r.get('Text') or ''
                to_remind += '\n'
        except Exception as e:
            possible_problems.append(('Reminder record {}'.format(r.get('Text')),
                                      str(e)))

    if to_remind:
        message += '\n\n'
        message += "You wanted me to remind you:\n"
        message += to_remind

    if possible_problems:
        message += textwrap.dedent("""\n
        The following sites may have had issues:\n""")
        for p in possible_problems:
            message += "- " + p[0] + ": " + str(p[1]).strip() + '\n'

    if '-d' not in sys.argv:
        try:
            yag = yagmail.SMTP(from_address, password)
            yag.send(to=recipients,
                     subject=subject,
                     contents=[message, datestring + '.zip'])
        except Exception as err:
            print('Could not send email. Skipping.')
            with open('automatt_error.txt', 'a') as f:
                f.write('Email issue: ' + repr(e) + '\n')
        try:
            send_to_discord(message, datestring + '.zip',
                            config['discord_token'], config['discord_channel_id'])
        except Exception as err:
            print('Could not post to Discord. Skipping.')
            with open('automat_error.txt', 'a') as f:
                f.write('Discord issue: ' + repr(e) + '\n')
        try:
            draft_post = html_doc.split('</h1>')[1]
            wp_tags = [rec.get('author') for rec in daily_records
                            if rec.get('author')]
            send_to_wordpress(draft_post, wp_tags, config['wordpress_token'])
        except Exception as err:
            print('Could not send email. Skipping.')
            with open('automatt_error.txt', 'a') as f:
                f.write('Wordpress issue: ' + repr(e) + '\n')
    else:
        print(message)
 

if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        with open('automatt_error.txt', 'a') as f:
            f.write(repr(e))
