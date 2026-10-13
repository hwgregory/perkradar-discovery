"""Public-source discovery, extraction, deduplication and audit reporting.

Usage: python -m discovery.engine --out catalogue
Never bypasses authentication, robots exclusions, rate limits, or captchas.
It reports opaque JS-only directories as unsupported instead of inventing coverage.
"""
import argparse
import hashlib
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
VERSION = 1
UA = 'PerkRadarDiscovery/0.1 (+public-source audit; polite; contact project maintainer)'
SESSION = requests.Session()
SESSION.headers.update({'User-Agent': UA, 'Accept':'text/html,application/xhtml+xml'})
ALLOWED_HOSTS = {
    'benefits.mynrma.com.au', 'www.mynrma.com.au',
    'shopping.qantas.com', 'www.everydayrewards.com.au',
    'www.americanexpress.com', 'optus.com.au', 'www.optus.com.au', 'www.everyday.com.au'
}
ROBOTS = {}


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def canonical_url(link):
    try:
        p=urlparse(link)
        if p.scheme != 'https' or p.hostname not in ALLOWED_HOSTS: return None
        return urlunparse((p.scheme, p.netloc.lower(), p.path.rstrip('/') or '/', '', '', ''))
    except ValueError:
        return None


def polite_allowed(url, *, robot_cache=None):
    robot_cache = ROBOTS if robot_cache is None else robot_cache
    p=urlparse(url); origin=p.scheme+'://'+p.netloc
    if p.hostname not in ALLOWED_HOSTS or p.scheme!='https': return False, 'host not allowlisted'
    if origin not in robot_cache:
        rp=RobotFileParser()
        rp.set_url(origin+'/robots.txt')
        try:
            response=SESSION.get(origin+'/robots.txt',timeout=12)
            if response.status_code in (401,403): return False, f'robots unavailable ({response.status_code})'
            if response.status_code in (404,410): rp.parse([])
            elif response.ok: rp.parse(response.text.splitlines())
            else: return False, f'robots server error ({response.status_code})'
            robot_cache[origin]=rp
        except requests.RequestException as exc:
            return False, f'robots lookup failed: {type(exc).__name__}'
    return bool(robot_cache[origin].can_fetch(UA,url)), 'robots policy'


def fetch(url, transport=None):
    """Returns (html, status); injectable for tests. Refuse redirects outside allowed hosts."""
    u=canonical_url(url)
    if not u: return None, 'blocked: unexpected host or protocol'
    if transport is not None: return transport(u)
    allowed,why=polite_allowed(u)
    if not allowed: return None, f'blocked: {why}'
    try:
        r=SESSION.get(u,timeout=18,allow_redirects=False)
        if r.is_redirect:
            dest=canonical_url(urljoin(u,r.headers.get('Location','')))
            if not dest: return None,'blocked redirect'
            allowed,why=polite_allowed(dest)
            if not allowed: return None,f'blocked redirect by {why}'
            r=SESSION.get(dest,timeout=18,allow_redirects=False)
        if r.status_code in (401,403,429): return None,f'restricted ({r.status_code})'
        if not r.ok: return None,f'http {r.status_code}'
        if 'html' not in r.headers.get('Content-Type','').lower(): return None,'not html'
        if len(r.content)>3_000_000: return None,'document too large'
        return r.text,'ok'
    except requests.RequestException as exc:
        return None,f'network failure: {type(exc).__name__}'


def rendered_fetch(url, *, timeout_ms=18000):
    """Optional JS-rendered public page adapter. Never logs in or bypasses restrictions.
    Invoked only after robots authorises the URL. Requires Playwright Chromium.
    """
    allowed,reason=polite_allowed(url)
    if not allowed:return None,f'blocked by {reason}'
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None,'Playwright not installed (run pip install playwright and playwright install chromium)'
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True)
            context=browser.new_context(user_agent=UA,locale='en-AU')
            page=context.new_page()
            page.goto(url,wait_until='domcontentloaded',timeout=timeout_ms)
            page.wait_for_timeout(1500)
            html=page.content()
            browser.close()
            return (html,'rendered') if len(html)<3_000_000 else (None,'rendered page too large')
    except Exception as exc:
        return None,'render failed: '+type(exc).__name__


def partner_candidates(html, base_url):
    """Only partner-specific destination links; no generic nav or inferred point rates."""
    soup=BeautifulSoup(html,'html.parser')
    host=urlparse(base_url).hostname
    found={}
    for a in soup.select('a[href]'):
        dest=canonical_url(urljoin(base_url,a.get('href','')))
        if not dest or dest==canonical_url(base_url):continue
        path=urlparse(dest).path.lower()
        # Qantas pages vary between brands and retailers. These candidates require review.
        if host=='shopping.qantas.com' and not re.search(r'/((brands?|stores?|retailers?|partners?)/[^/]+)$',path):continue
        if host=='www.everyday.com.au' and not re.search(r'/rewards/(partners|partner)/[^/]+',path):continue
        if host=='benefits.mynrma.com.au' and not re.search(r'/en/(product|d|p)/',path):continue
        label=text(a)
        if not 3<len(label)<140:continue
        found[dest]={'name':label,'url':dest}
    return list(found.values())


def partner_to_candidate(rec,source,date,source_url):
    name=rec['name']
    return {'id':source['provider']+'-partner-'+slug(name),
            'provider':source['provider'],'merchant':name,'kind':'partner',
            'title':name+' partner listing','claim':'Public partner listing detected; offer and earn rate require individual verification.',
            'value':None,'unit':None,'currency':'AUD','source_url':rec['url'],
            'directory_url':source_url,'redemption_url':rec['url'],
            'status':'needs_review','confidence':'candidate_link','eligibility':'Terms, participation and tier must be checked.',
            'tiers':[],'first_seen':date,'last_seen':date,'last_verified':None,
            'merchant_hosts':[],'match_terms':[name]}


def slug(value):
    return re.sub(r'[^a-z0-9]+','-',value.lower()).strip('-')[:100]


def text(element):
    return ' '.join(element.stripped_strings).strip() if element else ''


def link_candidates(html, base_url, kind):
    soup=BeautifulSoup(html,'html.parser')
    found={}
    for a in soup.select('a[href]'):
        label=text(a)[:170]
        url=canonical_url(urljoin(base_url,a.get('href','')))
        if not url or not label: continue
        path=urlparse(url).path.lower()
        if kind=='egift_directory':
            if not re.search(r'egift\s*card|gift\s*card',label,re.I):continue
            if not ('/d/egift-card/' in path or '/egift-card/' in path):continue
            name=re.sub(r'\s*(e?gift\s*card).*$', '',label,flags=re.I).strip()
            if not name or len(name)<2:continue
            found[url]={'label':label,'name':name,'url':url}
        elif kind=='attraction_directory':
            if not ('/product/' in path or re.search(r'^/en/[^/]+/[^/]+$',path)):continue
            if len(label)<5 or label.lower() in ('see more','view pricing','buy now'):continue
            found[url]={'label':label,'name':label,'url':url}
    return list(found.values())


def e_gift_to_offer(record, source_url, date):
    name=record['name'].replace('HI-FI','Hi-Fi')
    return {
        'id':'nrma-egift-'+slug(name),
        'provider':'nrma','merchant':name,'kind':'giftcard',
        'title':name+' eGift Card via NRMA',
        'claim':'Member eGift card is publicly listed. The current discount percentage is not verified.',
        'value':None,'unit':None,'currency':'AUD',
        'source_url':record['url'],'directory_url':source_url,
        'redemption_url':record['url'],
        'status':'published_unpriced','confidence':'listing_confirmed',
        'eligibility':'NRMA membership required; prices, redemption methods, fees and exclusions must be checked.',
        'tiers':[], 'first_seen':date,'last_seen':date,'last_verified':None,
        'merchant_hosts':[], 'match_terms':[name]
    }


def attraction_to_offer(record, source_url, date):
    name=record['name']
    return {
        'id':'nrma-attraction-'+slug(name),
        'provider':'nrma','merchant':name,'kind':'attraction',
        'title':name+' via NRMA',
        'claim':'Attraction ticket or experience listed in the NRMA member booking catalogue. Compare the same ticket and terms.',
        'value':None,'unit':None,'currency':'AUD',
        'source_url':record['url'],'directory_url':source_url,
        'redemption_url':record['url'],
        'status':'published_unpriced','confidence':'listing_confirmed',
        'eligibility':'Member availability, exact price and ticket conditions not verified.',
        'tiers':[], 'first_seen':date,'last_seen':date,'last_verified':None,
        'merchant_hosts':[], 'match_terms':[name]
    }


def add_verified_prices(offer, html, date):
    """Deliberately conservative: generic page amounts are not accepted as offers.
    A before/after pair must be explicitly tied to a named product, which varies by site.
    Leave pricing for review rather than claim false savings.
    """
    offer['detail_checked_at']=date
    soup=BeautifulSoup(html,'html.parser')
    h1=text(soup.find('h1'))
    if h1 and (slug(offer['merchant']) in slug(h1) or slug(h1) in slug(offer['merchant'])):
        offer['detail_title']=h1[:180]
    return offer


def collect(sources, existing, transport=None, *, sleep=0.7, max_details_override=None, render=False):
    date=now()
    offers={o['id']:dict(o) for o in existing}
    source_results=[]; review=[]
    for source in sources:
        url=source['url'];kind=source['kind'];ident=source['id']
        html,status=fetch(url,transport)
        if html is not None and source.get('needs_render') and render and transport is None:
            richer,render_status=rendered_fetch(url)
            if richer:
                html,status=richer,render_status
        result={'source':ident,'provider':source['provider'],'url':url,'status':status,'items':0,'new':0,'last_checked':date}
        if html is None:
            source_results.append(result);review.append({'source':ident,'reason':status,'url':url});continue
        if kind in ('egift_directory','attraction_directory'):
            found=link_candidates(html,url,kind)
            result['items']=len(found)
            if not found:
                result['status']='no results: site may render content via JavaScript'
                review.append({'source':ident,'reason':'No result links in public HTML; needs supported feed or separate authorised integration','url':url})
            limit=min(len(found),max_details_override if max_details_override is not None else source.get('max_details',50))
            for index,record in enumerate(found):
                candidate=e_gift_to_offer(record,url,date) if kind=='egift_directory' else attraction_to_offer(record,url,date)
                prev=offers.get(candidate['id'])
                if prev:
                    candidate['first_seen']=prev.get('first_seen',date)
                    if prev.get('status')=='verified' and prev.get('value') is not None:
                        # Do not overwrite approved price claims with less precise observations.
                        candidate.update({k:prev[k] for k in ('value','unit','claim','status','confidence','last_verified') if k in prev})
                    candidate['merchant_hosts']=prev.get('merchant_hosts',[])
                    candidate['match_terms']=prev.get('match_terms',candidate['match_terms'])
                else: result['new']+=1
                # When multiple sources list the same offer, retain the most specific URL.
                offers[candidate['id']]=candidate
                if index<limit and candidate['source_url']!=url:
                    detail,detail_status=fetch(record['url'],transport)
                    if detail:
                        add_verified_prices(candidate,detail,date)
                    elif detail_status!='ok':
                        review.append({'source':ident,'reason':'detail '+detail_status,'url':record['url']})
                    if transport is None and sleep:time.sleep(sleep)
        elif kind=='partner_directory':
            found=partner_candidates(html,url)
            result['items']=len(found)
            if not found:
                result['status']='no partner links extracted (JS rendering or source adapter needed)'
                review.append({'source':ident,'reason':'Partner listings not exposed as publicly retrievable links','url':url})
            for record in found:
                item=partner_to_candidate(record,source,date,url)
                prev=offers.get(item['id']);item['first_seen']=prev.get('first_seen',date) if prev else date
                if prev:
                    item['status']=prev.get('status','needs_review')
                    item['last_verified']=prev.get('last_verified')
                else:result['new']+=1
                offers[item['id']]=item
                review.append({'source':ident,'reason':'Verify partner/earning rate and official merchant domain','url':record['url']})
        else:
            result['status']='programme information checked; no structured offers exported'
            review.append({'source':ident,'reason':'Programme information only; personalised or tier-specific offers not publicly harvestable by this adapter','url':url})
        source_results.append(result)
        if transport is None and sleep:time.sleep(sleep)
    # Keep old records when a scrape fails. Mark stale separately after two weeks.
    for offer in offers.values():
        if offer.get('last_seen'):
            try:
                last=datetime.fromisoformat(offer['last_seen'])
                offer['freshness']='recent' if datetime.now(timezone.utc)-last <= timedelta(days=14) else 'stale'
            except ValueError:offer['freshness']='unknown'
    report={
        'generated_at':date,
        'source_counts':dict(Counter(s['status'] for s in source_results)),
        'total_discovered':len(offers),
        'by_provider':dict(Counter(o['provider'] for o in offers.values())),
        'by_status':dict(Counter(o['status'] for o in offers.values())),
        'sources':source_results,
        'review_queue':review[:300]
    }
    return {'schema_version':VERSION,'generated_at':date,'offers':sorted(offers.values(),key=lambda o:o['id'])},report


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--sources',default=str(ROOT/'sources.json'))
    parser.add_argument('--out',default=str(ROOT/'catalogue'))
    parser.add_argument('--render',action='store_true',help='Use optional Playwright Chromium for JS-rendered directories (respects robots.txt)')
    parser.add_argument('--fixtures',action='store_true',help='Offline self-test fixtures only; not a live refresh')
    parser.add_argument('--max-details',type=int,default=None)
    args=parser.parse_args()
    sources=json.loads(Path(args.sources).read_text())['sources']
    folder=Path(args.out);folder.mkdir(parents=True,exist_ok=True)
    existing=[];old=folder/'public-offers.json'
    # Bootstrap from first-party public directory listings, keeping their original
    # observation date. An unavailable scrape does NOT turn seeded data into fresh data.
    baseline=folder/'initial-public-directory-seed.json'
    if old.exists() or (baseline.exists() and not args.fixtures):
        selected=old if old.exists() else baseline
        try: existing=json.loads(selected.read_text()).get('offers',[])
        except (OSError,ValueError):pass
    transport=None
    if args.fixtures:
        fixtures={
            canonical_url(s['url']): ((ROOT/'fixtures'/(s['id']+'.html')).read_text(),'ok')
            for s in sources if (ROOT/'fixtures'/(s['id']+'.html')).exists()
        }
        transport=lambda url:fixtures.get(url,(None,'fixture unavailable'))
        sources=[s for s in sources if s['id'] in ('nrma_egift_cards','nrma_sydney_attractions')]
    catalog,report=collect(sources,existing,transport,max_details_override=args.max_details,sleep=0,render=args.render)
    if args.fixtures: # fixtures are tests, not authoritative live inventory
        output=folder/'fixture-offers.json';reportpath=folder/'fixture-report.json'
    else:
        output=folder/'public-offers.json';reportpath=folder/'coverage.json'
    output.write_text(json.dumps(catalog,indent=2,ensure_ascii=False)+'\n')
    reportpath.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
    print(f'{len(catalog["offers"])} offers; {len(report["review_queue"])} review items; wrote {output} and {reportpath}')

if __name__=='__main__':main()
