"""Lancer Parts Control — first operational release.
FastAPI + SQLite, server-side access control, no external API integrations enabled.
"""
import csv
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from starlette.middleware.sessions import SessionMiddleware

ROOT = Path(__file__).resolve().parent
INSTANCE = Path(os.environ.get('LPC_INSTANCE', ROOT / 'instance'))
INSTANCE.mkdir(parents=True, exist_ok=True)
DB = INSTANCE / 'lancer_parts.db'
TZ = ZoneInfo('America/Sao_Paulo')
ROLES = ('admin','gerente','vendedor','logistica','estoque','financeiro')
SALES = ('admin','gerente','vendedor')
OPS = ('admin','gerente','vendedor','logistica')
PROD_VIEW = ('admin','gerente','vendedor','logistica','estoque')
PROD_EDIT = ('admin','gerente','estoque','logistica')
BATCH_STATUS = ('Planejado','Em produção','Finalizado','Cancelado')
STAGES = ('Novo contato','Cliente interessado','Produto definido','Orçamento enviado','Em negociação','Aguardando pagamento','Venda concluída','Venda perdida','Retomar futuramente')
OPERATIONS = ('Pedido recebido','Aguardando pagamento','Pagamento aprovado','Aguardando separação','Em produção','Em preparação/acabamento','Pronto para envio','Etiqueta gerada','Aguardando coleta/postagem','Enviado','Em trânsito','Saiu para entrega','Entregue','Ocorrência logística','Cancelado/devolvido')
PAYMENTS = ('Pendente','Aprovado','Parcial','Reembolsado','Cancelado')
SHIPPING = ('Não enviado','Etiqueta pendente','Aguardando coleta','Em trânsito','Saiu para entrega','Entregue','Atrasado','Ocorrência')
PRIORITIES = ('Baixa','Média','Alta','Urgente')
RECURRENCES = ('','Diária','Semanal','Quinzenal','Mensal')
CATEGORIES = ('Retomar orçamento','Promoção','Estoque','Aguardar pagamento','Retorno futuro','Sem resposta','Outro')
VEHICLES = ('Lancer GT','Lancer CVT','Lancer MT','Lancer HL','Lancer HLE','Lancer HL-T','Ralliart','Evolution X','Outro')
LOSS_REASONS = ('','Preço','Frete','Prazo','Falta de estoque','Desistência','Concorrência','Outro')
NAV = [
 ('dashboard','Dashboard','layout-dashboard','/'),('agenda','Agenda e Avisos','calendar','/agenda'),('clients','CRM / Clientes','users','/clients'),
 ('pipeline','Funil de Vendas','columns-3','/opportunities'),('followups','Follow-ups','alarm-clock','/followups'),
 ('whatsapp','WhatsApp / Atendimento','message-circle','/modules/whatsapp'),
 ('orders','Pedidos','package','/orders'),('fulfillment','Controle de Pedidos','clipboard-check','/controle-pedidos'),('logistics','Logística / Rastreamento','truck','/modules/logistics'),
 ('stock','Estoque e Produtos','boxes','/modules/stock'),('production','Produção','wrench','/production'),
 ('finance','Financeiro','wallet','/modules/finance'),('marketing','Marketing / Campanhas','megaphone','/modules/marketing'),
 ('after-sales','Pós-venda','heart-handshake','/modules/after-sales'),('tasks','Tarefas / Planejamento','check-square','/tasks'),
 ('reports','Relatórios','chart-no-axes-column','/reports'),('insights','Insights Lancer Parts','lightbulb','/modules/insights'),
 ('integrations','Integrações','plug','/integrations'),('team','Equipe e Permissões','shield-check','/team'),
 ('settings','Configurações','settings','/settings')
]

# Stable local development secret; production should supply a strong env key.
secret_path = INSTANCE / '.session_secret'
if os.environ.get('LPC_SECRET_KEY'):
    SECRET = os.environ['LPC_SECRET_KEY']
elif secret_path.exists():
    SECRET = secret_path.read_text().strip()
else:
    SECRET = secrets.token_urlsafe(48)
    secret_path.write_text(SECRET)
    try: secret_path.chmod(0o600)
    except OSError: pass

app = FastAPI(title='Lancer Parts Control', docs_url=None, redoc_url=None)
app.add_middleware(SessionMiddleware, secret_key=SECRET, https_only=os.environ.get('LPC_SECURE_COOKIES')=='1', same_site='lax')
app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')

@contextmanager
def db():
    cn = sqlite3.connect(DB, timeout=15)
    cn.row_factory = sqlite3.Row
    cn.execute('PRAGMA foreign_keys=ON')
    cn.execute('PRAGMA journal_mode=WAL')
    try:
        yield cn
        cn.commit()
    except BaseException:
        cn.rollback()
        raise
    finally:
        cn.close()

def ensure_column(cn,table,column,ddl):
    if column not in {r['name'] for r in cn.execute(f'PRAGMA table_info({table})')}:
        cn.execute(f'ALTER TABLE {table} ADD COLUMN {column} {ddl}')

with db() as cn:
    cn.executescript((ROOT / 'schema.sql').read_text(encoding='utf-8'))
    # Migração para bancos criados em versões anteriores.
    for _col,_ddl in (('packed','INTEGER NOT NULL DEFAULT 0'),('packed_at','TEXT'),('customer_notified','INTEGER NOT NULL DEFAULT 0'),('shipped_at','TEXT'),('delivered_at','TEXT')):
        ensure_column(cn,'orders',_col,_ddl)
    ensure_column(cn,'tasks','recurrence','TEXT')

jinja = Environment(loader=FileSystemLoader(ROOT/'templates'), autoescape=select_autoescape(['html','xml']))


def now(): return datetime.now(TZ)
def iso_now(): return now().isoformat(timespec='seconds')
def digits(v): return re.sub(r'\D','',str(v or ''))
def phone_key(v):
    d=digits(v)
    return d[2:] if d.startswith('55') and len(d) in (12,13) else d
def money_to_cents(v):
    s = str(v or '0').strip().replace('R$','').replace(' ','')
    if ',' in s: s = s.replace('.','').replace(',','.')
    try: n=round(float(s)*100)
    except (ValueError,OverflowError): raise ValueError('Informe um valor monetário válido.')
    if n<0 or n>10**12: raise ValueError('Valor fora do limite.')
    return n

def fmt_money(n): return f'R$ {int(n or 0)/100:,.2f}'.replace(',','@').replace('.',',').replace('@','.')
def fmt_date(s):
    if not s:return '—'
    try:return datetime.fromisoformat(s.replace('Z','+00:00')).strftime('%d/%m/%Y %H:%M' if 'T' in s or ' ' in s else '%d/%m/%Y')
    except ValueError:return s

def fmt_phone(s): return digits(s)
def wa_number(phone):
    d=digits(phone)
    if len(d) in (10,11):d='55'+d
    return d if 10<=len(d)<=15 else ''
def wa_link(phone,text=''):
    n=wa_number(phone)
    if not n:return ''
    return f'https://wa.me/{n}'+(('?text='+quote(text)) if text else '')
def wa_greeting(name,store_number=''):
    first=(name or '').strip().split(' ')[0] if name else ''
    msg=f'Olá{", "+first if first else ""}! Aqui é da Lancer Parts.'
    if store_number:msg+=f' Nosso WhatsApp: {fmt_br_phone(store_number)}'
    return msg
def fmt_br_phone(v):
    d=digits(v)
    if d.startswith('55') and len(d) in (12,13):d=d[2:]
    if len(d)==11:return f'({d[:2]}) {d[2:7]}-{d[7:]}'
    if len(d)==10:return f'({d[:2]}) {d[2:6]}-{d[6:]}'
    return v or ''
def get_setting(c,key,default=''):
    row=qone(c,'SELECT value FROM app_settings WHERE key=?',(key,))
    return row['value'] if row and row['value'] is not None else default
def qone(c,sql,args=()):return c.execute(sql,args).fetchone()
def qall(c,sql,args=()):return c.execute(sql,args).fetchall()
def record(row):return dict(row) if row else None

def hash_password(password):
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f'scrypt${salt.hex()}${derived.hex()}'

def verify_password(pw,stored):
    try:
        kind,salt,digest=stored.split('$')
        if kind!='scrypt':return False
        calc=hashlib.scrypt(pw.encode(),salt=bytes.fromhex(salt),n=2**14,r=8,p=1)
        return hmac.compare_digest(calc,bytes.fromhex(digest))
    except (ValueError,TypeError):return False


def csrf(request):
    if not request.session.get('csrf'):
        request.session['csrf']=secrets.token_urlsafe(24)
    return request.session['csrf']

def validate_csrf(request,data):
    token = data.get('csrf') or request.headers.get('x-csrf-token','')
    if not token or not hmac.compare_digest(str(token), str(request.session.get('csrf',''))):
        raise HTTPException(403, 'Token de segurança inválido. Atualize a página e tente novamente.')


def current_user(request):
    uid = request.session.get('uid')
    if not uid: return None
    with db() as c:
        row=qone(c,'SELECT id,name,email,role,active FROM users WHERE id=?',(uid,))
        return record(row) if row and row['active'] else None

def require(request, allowed=None):
    user=current_user(request)
    if not user:raise HTTPException(401,'Sessão expirada. Entre novamente.')
    if allowed and user['role'] not in allowed:raise HTTPException(403,'Você não tem permissão para esta ação.')
    return user

def owner_clause(user, field='owner_id'):
    return (f' AND {field}=?',[user['id']]) if user['role']=='vendedor' else ('',[])

def client_allowed(c,user,client_id):
    row=qone(c,'SELECT * FROM clients WHERE id=? AND archived=0',(client_id,))
    if not row:raise HTTPException(404,'Cliente não encontrado.')
    if user['role']=='vendedor' and row['owner_id']!=user['id']:
        raise HTTPException(403,'Cliente atribuído a outro vendedor.')
    return row

def audit(c,user,entity,entity_id,action,before=None,after=None):
    c.execute('INSERT INTO audit_logs (user_id,entity,entity_id,action,before_json,after_json) VALUES (?,?,?,?,?,?)',
        (user['id'],entity,entity_id,action,json.dumps(before,ensure_ascii=False,default=str) if before else None,
         json.dumps(after,ensure_ascii=False,default=str) if after else None))

def activity(c,user,client_id,kind,description):
    c.execute('INSERT INTO activities (client_id,kind,description,user_id) VALUES (?,?,?,?)',(client_id,kind,description,user['id']))
    c.execute('UPDATE clients SET last_contact_at=?,updated_at=? WHERE id=?',(iso_now(),iso_now(),client_id))

def redirect(url):return RedirectResponse(url,status_code=303)
def val(data,key,limit=2000):return str(data.get(key,'') or '').strip()[:limit]
def intval(v, default=0):
    try:return int(v)
    except (ValueError,TypeError):return default

def clean_date(v):
    s=str(v or '').strip()
    if not s:return None
    try:return datetime.fromisoformat(s).isoformat(timespec='minutes')
    except ValueError:raise ValueError('Data e horário inválidos.')

def clean_day(v):
    s=str(v or '').strip()
    if not s:return None
    try:return datetime.strptime(s,'%Y-%m-%d').strftime('%Y-%m-%d')
    except ValueError:raise ValueError('Data inválida.')

def assignee(c,user,p):
    if user['role']=='vendedor':return user['id']
    uid=intval(p)
    if uid and qone(c,'SELECT id FROM users WHERE id=? AND active=1',(uid,)):return uid
    return user['id']

def render(request, template, **kwargs):
    user=current_user(request)
    if not user and template!='login.html':return redirect('/login')
    alerts_count=0
    with db() as _c:
        store_wa=get_setting(_c,'store_whatsapp')
        if user:
            try:alerts_count=sum(1 for a in build_alerts(_c,user) if a['level']<=1)
            except sqlite3.Error:alerts_count=0
    ctx = dict(request=request,user=user,nav=NAV,store_whatsapp=store_wa,store_whatsapp_fmt=fmt_br_phone(store_wa),
      wa=lambda phone,name='':wa_link(phone,wa_greeting(name,store_wa)),wa_text=wa_link,alerts_count=alerts_count,recurrences=RECURRENCES,csrf=csrf(request),fmt_money=fmt_money,fmt_date=fmt_date,
      fmt_phone=fmt_phone,stages=STAGES,operations=OPERATIONS,payments=PAYMENTS,shipping_states=SHIPPING,
      priorities=PRIORITIES,vehicles=VEHICLES,categories=CATEGORIES,loss_reasons=LOSS_REASONS,
      now=now(), success=request.query_params.get('success'), error=request.query_params.get('error'))
    ctx.update(kwargs)
    return HTMLResponse(jinja.get_template(template).render(**ctx))

@app.exception_handler(HTTPException)
async def http_error(request, exc):
    if exc.status_code==401:return redirect('/login')
    if request.url.path.startswith('/api/'):
        return JSONResponse({'error':exc.detail},status_code=exc.status_code)
    response=render(request,'error.html',code=exc.status_code,detail=exc.detail,section='')
    response.status_code=exc.status_code
    return response

@app.middleware('http')
async def headers(request,call_next):
    response=await call_next(request)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['X-Frame-Options']='DENY'
    response.headers['Referrer-Policy']='strict-origin-when-cross-origin'
    response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: https:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    response.headers['Cache-Control']='no-store'
    return response

@app.get('/login')
def login(request:Request):
    if current_user(request):return redirect('/')
    return render(request,'login.html',section='login')

@app.post('/login')
async def login_post(request:Request):
    data=await request.form()
    validate_csrf(request,data)
    email=val(data,'email',250).lower()
    password=val(data,'password',500)
    with db() as c:
        u=qone(c,'SELECT * FROM users WHERE email=? AND active=1',(email,))
    if not u or not verify_password(password,u['password_hash']):
        return render(request,'login.html',section='login',login_error='E-mail ou senha inválidos.')
    request.session.clear()
    request.session['uid']=u['id']
    request.session['csrf']=secrets.token_urlsafe(24)
    return redirect('/')

@app.post('/logout')
async def logout(request:Request):
    data=await request.form();validate_csrf(request,data)
    request.session.clear();return redirect('/login')

# ------------------- CONTROLE DE PEDIDOS / AVISOS / AGENDA -------------------
WEEKDAYS=('segunda','terça','quarta','quinta','sexta','sábado','domingo')
SENT_OPS=('Enviado','Em trânsito','Saiu para entrega','Entregue')
READY_OPS=('Pronto para envio','Etiqueta gerada','Aguardando coleta/postagem')
PREP_OPS=('Aguardando separação','Em produção','Em preparação/acabamento')
LEVELS={0:('Crítico','pill-red'),1:('Atenção','pill-yellow'),2:('Lembrete','pill-blue')}

def parse_dt(v):
    if not v:return None
    try:d=datetime.fromisoformat(str(v).replace('Z','+00:00'))
    except ValueError:return None
    return d if d.tzinfo else d.replace(tzinfo=TZ)

def age_days(created_at):
    """created_at usa CURRENT_TIMESTAMP (UTC)."""
    try:d=datetime.fromisoformat(str(created_at))
    except ValueError:return 0
    if d.tzinfo is None:d=d.replace(tzinfo=timezone.utc)
    return max(0,(datetime.now(timezone.utc)-d).days)

def human_delta(td):
    secs=int(abs(td.total_seconds()))
    if secs<3600:return f'{max(1,secs//60)} min'
    if secs<86400:return f'{secs//3600} h'
    days=secs//86400
    return f'{days} dia' + ('s' if days!=1 else '')

def order_control(o):
    """Estado derivado do pedido: etapa, checklist de envio e urgência."""
    op=o['operational_status'];ship=o['shipping_status'];pay=o['payment_status']
    cancelled=op=='Cancelado/devolvido' or pay in ('Cancelado','Reembolsado')
    delivered=op=='Entregue' or ship=='Entregue'
    sent=delivered or op in SENT_OPS or ship in ('Em trânsito','Saiu para entrega')
    problem=(op=='Ocorrência logística' or ship in ('Atrasado','Ocorrência')) and not delivered and not cancelled
    ready=op in READY_OPS or ship in ('Etiqueta pendente','Aguardando coleta')
    prepping=op in PREP_OPS
    paid=pay=='Aprovado'
    closed=delivered or cancelled
    due=parse_dt(o['due_at']);overdue=bool(due) and due<now() and not closed
    prepared=sent or (op in OPERATIONS and OPERATIONS.index(op)>=OPERATIONS.index('Pronto para envio') and not cancelled)
    packed=bool(o['packed']);notified=bool(o['customer_notified']);tracking=bool((o['tracking_code'] or '').strip())
    if cancelled:stage,cls='Cancelado','pill-red'
    elif delivered:stage,cls='Entregue','pill-green'
    elif problem:stage,cls='Ocorrência','pill-red'
    elif sent:stage,cls='Enviado','pill-blue'
    elif ready:stage,cls='Pronto p/ envio','pill-yellow'
    elif prepping:stage,cls='Em preparo',''
    elif not paid:stage,cls='Aguardando pagamento','pill-yellow'
    else:stage,cls='A iniciar',''
    if closed:rank=9
    elif problem or overdue:rank=0
    elif stage=='Pronto p/ envio':rank=1
    elif stage in ('Em preparo','A iniciar'):rank=2
    elif stage=='Aguardando pagamento':rank=3
    else:rank=4
    steps=[('Pago',paid),('Preparado',prepared),('Embalado',packed),('Rastreio',tracking),('Enviado',sent),('Entregue',delivered),('Cliente avisado',notified)]
    return dict(stage=stage,stage_class=cls,steps=steps,overdue=overdue,problem=problem,sent=sent,delivered=delivered,cancelled=cancelled,
                paid=paid,closed=closed,rank=rank,tracking=tracking,packed=packed,notified=notified,age_days=age_days(o['created_at']),due=due)

ORDER_SELECT=("SELECT o.*,cl.name client_name,cl.phone client_phone,"
              "(SELECT GROUP_CONCAT(oi.quantity||'x '||oi.product,' · ') FROM order_items oi WHERE oi.order_id=o.id) items_text "
              "FROM orders o JOIN clients cl ON cl.id=o.client_id WHERE 1=1")

def control_rows(c,u,limit=600):
    if u['role'] not in OPS:return []
    scope,args=owner_clause(u,'o.owner_id')
    rows=[]
    for r in qall(c,ORDER_SELECT+scope+' ORDER BY o.created_at DESC LIMIT ?',(*args,limit)):
        d=dict(r);d['ctl']=order_control(d);rows.append(d)
    return rows

def build_alerts(c,u):
    """Avisos priorizados: 0 = crítico, 1 = atenção, 2 = lembrete."""
    ref=now();end_today=ref.strftime('%Y-%m-%dT23:59');out=[];role=u['role']
    def add(level,kind,icon,title,detail,url,when=None):
        out.append(dict(level=level,kind=kind,icon=icon,title=title,detail=detail,url=url,when=when,label=LEVELS[level][0],css=LEVELS[level][1]))
    if role in SALES:
        scope=' AND f.owner_id=?' if role=='vendedor' else ''
        for f in qall(c,"SELECT f.*,cl.name client_name FROM followups f JOIN clients cl ON cl.id=f.client_id WHERE f.status='Pendente' AND cl.archived=0 AND f.due_at<=?"+scope+" ORDER BY f.due_at LIMIT 100",(end_today,*([u['id']] if scope else []))):
            due=parse_dt(f['due_at'])
            if not due:continue
            late=ref-due;over=late.total_seconds()>0;high=f['priority'] in ('Alta','Urgente')
            level=(0 if (late.days>=1 or high) else 1) if over else 2
            add(level,'Follow-up','◷',f"Retornar para {f['client_name']} · {f['product']}",
                (f'Atrasado há {human_delta(late)}' if over else f'Hoje às {due:%H:%M}')+f" · prioridade {f['priority']}",f"/followups/{f['id']}/edit",due)
    scope='' if role in ('admin','gerente') else ' AND t.assignee_id=?'
    for t in qall(c,"SELECT t.* FROM tasks t WHERE t.status='Pendente' AND t.due_at IS NOT NULL AND t.due_at<=?"+scope+" ORDER BY t.due_at LIMIT 100",(end_today,*([u['id']] if scope else []))):
        due=parse_dt(t['due_at'])
        if not due:continue
        late=ref-due;over=late.total_seconds()>0;high=t['priority'] in ('Alta','Urgente')
        level=(0 if (late.days>=1 or high) else 1) if over else (1 if high else 2)
        add(level,'Tarefa','☑',t['title'],(f'Atrasada há {human_delta(late)}' if over else f'Hoje às {due:%H:%M}')+f" · prioridade {t['priority']}",'/tasks',due)
    if role in OPS:
        for o in control_rows(c,u):
            ctl=o['ctl']
            if ctl['closed']:continue
            num=o['order_number'];url=f"/orders/{o['id']}";who=o['client_name']
            if ctl['problem']:add(0,'Pedido','▤',f'Ocorrência logística no pedido #{num}',f'{who} · confira a transportadora e avise o cliente',url)
            if ctl['overdue']:add(0,'Pedido','▤',f'Prazo do pedido #{num} vencido',f"{who} · venceu há {human_delta(now()-ctl['due'])} · etapa: {ctl['stage']}",url,ctl['due'])
            elif ctl['due'] and not ctl['sent'] and ctl['due']-ref<=timedelta(hours=48):
                add(1,'Pedido','▤',f'Pedido #{num} vence em {human_delta(ctl["due"]-ref)}',f"{who} · etapa: {ctl['stage']}",url,ctl['due'])
            if ctl['paid'] and not ctl['sent'] and ctl['age_days']>=2:
                add(0 if ctl['age_days']>=5 else 1,'Pedido','▤',f'Pedido #{num} pago e ainda não enviado',f"{who} · criado há {ctl['age_days']} dias · etapa: {ctl['stage']}",url)
            if ctl['stage']=='Aguardando pagamento' and ctl['age_days']>=3:
                add(1,'Pedido','▤',f'Pedido #{num} aguardando pagamento há {ctl["age_days"]} dias',f'{who} · cobrar o cliente ou cancelar',url)
            if ctl['sent'] and not ctl['tracking']:add(1,'Pedido','▤',f'Pedido #{num} enviado sem código de rastreio',f'{who} · cadastre o rastreio no pedido',url)
            elif ctl['sent'] and not ctl['notified']:add(2,'Pedido','▤',f'Avise {who} sobre o envio do pedido #{num}','Marque “Cliente avisado” depois do contato',url)
    if role in PROD_VIEW:
        for b in qall(c,"SELECT b.*,p.name product FROM production_batches b JOIN products p ON p.id=b.product_id WHERE b.status IN ('Planejado','Em produção') AND b.due_at IS NOT NULL"):
            due=parse_dt(b['due_at'])
            if not due:continue
            if due<ref:add(0,'Produção','⚙',f"Lote {b['code']} atrasado",f"{b['product']} · {b['qty_finished']}/{b['qty_planned']} finalizadas · venceu há {human_delta(ref-due)}",f"/production/{b['id']}",due)
            elif due-ref<=timedelta(hours=48):add(1,'Produção','⚙',f"Lote {b['code']} vence em {human_delta(due-ref)}",f"{b['product']} · {b['qty_finished']}/{b['qty_planned']} finalizadas",f"/production/{b['id']}",due)
    far=ref+timedelta(days=3650)
    out.sort(key=lambda a:(a['level'],a['when'] or far))
    return out

def agenda_items(c,u):
    """Cronograma: tudo que tem prazo (e tarefas sem prazo), em ordem de data."""
    ref=now();items=[];role=u['role']
    def add(when,kind,icon,title,detail,url,priority=''):
        items.append(dict(when=when,kind=kind,icon=icon,title=title,detail=detail,url=url,priority=priority))
    if role in SALES:
        scope=' AND f.owner_id=?' if role=='vendedor' else ''
        for f in qall(c,"SELECT f.*,cl.name client_name FROM followups f JOIN clients cl ON cl.id=f.client_id WHERE f.status='Pendente' AND cl.archived=0"+scope,([u['id']] if scope else [])):
            add(parse_dt(f['due_at']),'Follow-up','◷',f"{f['client_name']} · {f['product']}",f"Retorno · {f['category'] or 'sem categoria'}",f"/followups/{f['id']}/edit",f['priority'])
    scope='' if role in ('admin','gerente') else ' AND t.assignee_id=?'
    for t in qall(c,"SELECT t.*,u.name assignee FROM tasks t LEFT JOIN users u ON u.id=t.assignee_id WHERE t.status='Pendente'"+scope,([u['id']] if scope else [])):
        add(parse_dt(t['due_at']),'Tarefa','☑',t['title'],f"{t['assignee'] or 'sem responsável'}"+(f" · repete: {t['recurrence']}" if t['recurrence'] else ''),'/tasks',t['priority'])
    if role in OPS:
        for o in control_rows(c,u):
            ctl=o['ctl']
            if ctl['closed'] or not ctl['due']:continue
            add(ctl['due'],'Pedido','▤',f"Prazo do pedido #{o['order_number']} · {o['client_name']}",f"Etapa: {ctl['stage']}",f"/orders/{o['id']}")
    if role in PROD_VIEW:
        for b in qall(c,"SELECT b.*,p.name product FROM production_batches b JOIN products p ON p.id=b.product_id WHERE b.status IN ('Planejado','Em produção') AND b.due_at IS NOT NULL"):
            add(parse_dt(b['due_at']),'Produção','⚙',f"Lote {b['code']} · {b['product']}",f"{b['qty_finished']}/{b['qty_planned']} finalizadas",f"/production/{b['id']}")
    far=ref+timedelta(days=3650)
    items.sort(key=lambda i:(i['when'] or far))
    return items

def group_agenda(items,horizon_days=30):
    today=now().date();buckets={}
    order=['Atrasados','Hoje','Amanhã']
    def put(label,item):
        buckets.setdefault(label,[]).append(item)
    for it in items:
        if it['when'] is None:put('Sem prazo definido',it);continue
        d=it['when'].date();delta=(d-today).days
        if delta<0:put('Atrasados',it)
        elif delta==0:put('Hoje',it)
        elif delta==1:put('Amanhã',it)
        elif delta<=min(7,horizon_days):
            label=f'{WEEKDAYS[d.weekday()].capitalize()}, {d:%d/%m}'
            if label not in order:order.append(label)
            put(label,it)
        elif delta<=horizon_days:put('Mais tarde (até 30 dias)',it)
    order+=['Mais tarde (até 30 dias)','Sem prazo definido']
    return [(k,buckets[k]) for k in order if k in buckets]

CONTROL_TABS=(('abertos','Em aberto'),('pagamento','Aguardando pagamento'),('preparo','A preparar'),('prontos','Prontos p/ envio'),('enviados','Enviados'),
              ('problemas','Atrasos e ocorrências'),('semrastreio','Sem rastreio'),('entregues','Entregues'),('cancelados','Cancelados'),('todos','Todos'))
def control_match(key,r):
    ctl=r['ctl']
    return {'abertos':not ctl['closed'],'pagamento':ctl['stage']=='Aguardando pagamento','preparo':ctl['stage'] in ('A iniciar','Em preparo'),
            'prontos':ctl['stage']=='Pronto p/ envio','enviados':ctl['stage']=='Enviado','problemas':(ctl['problem'] or ctl['overdue']) and not ctl['closed'],
            'semrastreio':ctl['sent'] and not ctl['tracking'] and not ctl['closed'],'entregues':ctl['delivered'],'cancelados':ctl['cancelled'],'todos':True}.get(key,False)

def safe_next(v,default):
    v=str(v or '')
    return v if v.startswith('/') and not v.startswith('//') and '\\' not in v else default

@app.get('/controle-pedidos')
def order_control_page(request:Request,stage:str='abertos',q:str=''):
    u=require(request,OPS)
    with db() as c:rows=control_rows(c,u)
    if stage not in dict(CONTROL_TABS):stage='abertos'
    counts={k:sum(1 for r in rows if control_match(k,r)) for k,_ in CONTROL_TABS}
    needle=q.strip().lower()
    shown=[r for r in rows if control_match(stage,r) and (not needle or needle in (r['order_number'] or '').lower() or needle in (r['client_name'] or '').lower() or needle in (r['tracking_code'] or '').lower())]
    shown.sort(key=lambda r:(r['ctl']['rank'],r['ctl']['due'] or now()+timedelta(days=3650)))
    return render(request,'controle_pedidos.html',section='fulfillment',title='Controle de Pedidos',rows=shown[:300],tabs=CONTROL_TABS,counts=counts,stage=stage,q=q,next_url='/controle-pedidos'+('?stage='+stage if stage!='abertos' else ''))

@app.post('/orders/{oid}/quick')
async def order_quick(request:Request,oid:int):
    u=require(request,OPS);data=await request.form();validate_csrf(request,data)
    action=val(data,'action',20);nxt=safe_next(data.get('next'),'/controle-pedidos')
    def back(msg,err=False):return redirect(nxt+('&' if '?' in nxt else '?')+('error=' if err else 'success=')+quote(msg))
    with db() as c:
        o=qone(c,'SELECT * FROM orders WHERE id=?',(oid,))
        if not o:raise HTTPException(404,'Pedido não encontrado')
        if u['role']=='vendedor' and o['owner_id']!=u['id']:raise HTTPException(403,'Pedido atribuído a outro vendedor')
        ctl=order_control(dict(o));num=o['order_number'];stamp=iso_now()
        if ctl['cancelled']:return back('Pedido cancelado: não é possível alterar o controle.',True)
        if action=='packed':
            new=0 if o['packed'] else 1
            c.execute('UPDATE orders SET packed=?,packed_at=?,updated_at=? WHERE id=?',(new,stamp if new else None,stamp,oid))
            audit(c,u,'orders',oid,'packed',{'packed':o['packed']},{'packed':new});msg='Embalagem marcada' if new else 'Embalagem desmarcada'
        elif action=='notified':
            new=0 if o['customer_notified'] else 1
            c.execute('UPDATE orders SET customer_notified=?,updated_at=? WHERE id=?',(new,stamp,oid))
            audit(c,u,'orders',oid,'notified',{'customer_notified':o['customer_notified']},{'customer_notified':new})
            if new:activity(c,u,o['client_id'],'Pedido',f'Cliente avisado sobre o pedido {num}')
            msg='Cliente marcado como avisado' if new else 'Aviso ao cliente desmarcado'
        elif action=='shipped':
            if ctl['delivered']:return back('Pedido já entregue.',True)
            if o['payment_status'] not in ('Aprovado','Parcial'):return back('Pagamento ainda não aprovado: confirme o pagamento antes de enviar.',True)
            tracking=val(data,'tracking_code',100) or (o['tracking_code'] or '')
            ship=o['shipping_status'] if o['shipping_status'] in ('Em trânsito','Saiu para entrega') else 'Em trânsito'
            c.execute("UPDATE orders SET operational_status='Enviado',shipping_status=?,tracking_code=?,packed=1,packed_at=COALESCE(packed_at,?),shipped_at=COALESCE(shipped_at,?),updated_at=? WHERE id=?",(ship,tracking,stamp,stamp,stamp,oid))
            audit(c,u,'orders',oid,'shipped',{'operational_status':o['operational_status'],'shipping_status':o['shipping_status'],'tracking_code':o['tracking_code']},{'operational_status':'Enviado','shipping_status':ship,'tracking_code':tracking})
            activity(c,u,o['client_id'],'Pedido',f'Pedido {num} marcado como enviado');msg='Pedido marcado como enviado'
        elif action=='delivered':
            c.execute("UPDATE orders SET operational_status='Entregue',shipping_status='Entregue',delivered_at=COALESCE(delivered_at,?),updated_at=? WHERE id=?",(stamp,stamp,oid))
            audit(c,u,'orders',oid,'delivered',{'operational_status':o['operational_status'],'shipping_status':o['shipping_status']},{'operational_status':'Entregue','shipping_status':'Entregue'})
            activity(c,u,o['client_id'],'Pedido',f'Pedido {num} entregue');msg='Pedido marcado como entregue'
        else:raise HTTPException(400,'Ação inválida')
    return back(msg)

@app.get('/agenda')
def agenda(request:Request):
    u=require(request)
    with db() as c:
        alerts=build_alerts(c,u);items=agenda_items(c,u)
    return render(request,'agenda.html',section='agenda',title='Agenda e Avisos',alerts=alerts,groups=group_agenda(items),crit=sum(1 for a in alerts if a['level']==0),attn=sum(1 for a in alerts if a['level']==1))

@app.get('/')
def dashboard(request:Request):
    u=require(request)
    today=now().date().isoformat()
    with db() as c:
        alerts=build_alerts(c,u);items=agenda_items(c,u);rows=control_rows(c,u)
        open_opps=qone(c,"SELECT COUNT(*) n FROM opportunities WHERE stage NOT IN ('Venda concluída','Venda perdida')"+owner_clause(u,'owner_id')[0],owner_clause(u,'owner_id')[1])['n'] if u['role'] in SALES else 0
    open_rows=[r for r in rows if not r['ctl']['closed']]
    open_rows.sort(key=lambda r:(r['ctl']['rank'],r['ctl']['due'] or now()+timedelta(days=3650)))
    kpi=dict(
        crit=sum(1 for a in alerts if a['level']==0),attn=sum(1 for a in alerts if a['level']==1),
        followups=sum(1 for a in alerts if a['kind']=='Follow-up' and a['level']<=1),
        tasks=sum(1 for a in alerts if a['kind']=='Tarefa'),
        to_ship=sum(1 for r in open_rows if r['ctl']['stage'] in ('A iniciar','Em preparo','Pronto p/ envio')),
        in_transit=sum(1 for r in open_rows if r['ctl']['stage']=='Enviado'),
        waiting_pay=sum(1 for r in open_rows if r['ctl']['stage']=='Aguardando pagamento'),open_opps=open_opps)
    near=[g for g in group_agenda(items,horizon_days=3) if g[0]!='Sem prazo definido']
    return render(request,'dashboard.html',section='dashboard',kpi=kpi,alerts=alerts[:8],alerts_total=len(alerts),agenda_groups=near,rows=open_rows[:8],open_total=len(open_rows),next_url='/')

@app.get('/clients')
def clients(request:Request,q:str='',origin:str='',page:int=1):
    u=require(request,SALES)
    page=max(1,min(page,99999));filters=['archived=0'];args=[]
    if q:
        filters.append('(name LIKE ? OR phone_normalized LIKE ? OR email LIKE ?)')
        args += ['%'+q+'%','%'+phone_key(q)+'%','%'+q+'%']
    if origin:filters.append('origin=?');args.append(origin)
    if u['role']=='vendedor':filters.append('owner_id=?');args.append(u['id'])
    where=' AND '.join(filters)
    with db() as c:
        total=qone(c,'SELECT COUNT(*) n FROM clients WHERE '+where,args)['n']
        rows=qall(c,'SELECT cl.*,u.name owner FROM clients cl LEFT JOIN users u ON u.id=cl.owner_id WHERE '+where+' ORDER BY cl.created_at DESC LIMIT 25 OFFSET ?',(*args,(page-1)*25))
    return render(request,'clients.html',section='clients',rows=rows,q=q,origin=origin,page=page,total=total)

@app.get('/clients/new')
def client_new(request:Request):
    u=require(request,SALES)
    with db() as c: users=qall(c,'SELECT id,name FROM users WHERE active=1 AND role IN (\'admin\',\'gerente\',\'vendedor\') ORDER BY name')
    return render(request,'client_form.html',section='clients',item={},users=users,is_edit=False)

async def save_client(request, client_id=None):
    u=require(request,SALES);data=await request.form();validate_csrf(request,data)
    name=val(data,'name',150)
    if len(name)<2:return redirect(request.url.path+'?error=Informe um nome válido')
    phone=val(data,'phone',30); normalized=phone_key(phone)
    if normalized and len(normalized)<10:return redirect(request.url.path+'?error=Telefone inválido')
    fields=['name','phone','email','document','city','state','address','vehicle_model','modifications','interests','notes','tags','origin']
    values={f:val(data,f,3000 if f in ['notes','modifications','interests'] else 255) for f in fields}
    values['name']=name;values['phone_normalized']=normalized or None
    values['vehicle_year']=intval(data.get('vehicle_year')) or None
    if values['vehicle_year'] and not 1980<=values['vehicle_year']<=2100:
        return redirect(request.url.path+'?error=Ano inválido')
    with db() as c:
        old=record(client_allowed(c,u,client_id)) if client_id else None
        if normalized:
            found=qone(c,'SELECT id,name FROM clients WHERE phone_normalized=? AND archived=0 AND id!=?',(normalized,client_id or -1))
            if found:return redirect(request.url.path+'?error='+quote(f'Possível duplicidade: {found["name"]} (cliente #{found["id"]}). Confira antes de salvar.'))
        if values['document']:
            found=qone(c,'SELECT id FROM clients WHERE document=? AND archived=0 AND id!=?',(values['document'],client_id or -1))
            if found:return redirect(request.url.path+'?error=CPF/CNPJ já cadastrado')
        values['owner_id']=assignee(c,u,data.get('owner_id'))
        if client_id:
            cols=','.join(f'{k}=?' for k in values)
            c.execute(f'UPDATE clients SET {cols},updated_at=? WHERE id=?',(*values.values(),iso_now(),client_id))
            audit(c,u,'clients',client_id,'update',old,values)
            msg='Cliente atualizado'
        else:
            cols=','.join(values)
            cur=c.execute(f'INSERT INTO clients ({cols}) VALUES ({",".join("?" for _ in values)})',list(values.values()))
            client_id=cur.lastrowid;audit(c,u,'clients',client_id,'create',None,values)
            msg='Cliente cadastrado'
    return redirect(f'/clients/{client_id}?success='+quote(msg))

@app.post('/clients/new')
async def create_client(request:Request):return await save_client(request)
@app.get('/clients/{client_id}/edit')
def client_edit(request:Request,client_id:int):
    u=require(request,SALES)
    with db() as c:
        item=client_allowed(c,u,client_id)
        users=qall(c,"SELECT id,name FROM users WHERE active=1 AND role IN ('admin','gerente','vendedor') ORDER BY name")
    return render(request,'client_form.html',section='clients',item=item,users=users,is_edit=True)
@app.post('/clients/{client_id}/edit')
async def update_client(request:Request,client_id:int):return await save_client(request,client_id)
@app.get('/clients/{client_id}')
def client_detail(request:Request,client_id:int):
    u=require(request,SALES)
    with db() as c:
        item=client_allowed(c,u,client_id)
        timeline=qall(c,'SELECT a.*,u.name author FROM activities a LEFT JOIN users u ON u.id=a.user_id WHERE a.client_id=? ORDER BY a.created_at DESC',(client_id,))
        opps=qall(c,'SELECT * FROM opportunities WHERE client_id=? ORDER BY created_at DESC',(client_id,))
        followups=qall(c,'SELECT * FROM followups WHERE client_id=? ORDER BY due_at DESC',(client_id,))
        orders=qall(c,'SELECT * FROM orders WHERE client_id=? ORDER BY created_at DESC',(client_id,))
    return render(request,'client_detail.html',section='clients',item=item,timeline=timeline,opps=opps,followups=followups,orders=orders)
@app.post('/clients/{client_id}/activity')
async def client_activity(request:Request,client_id:int):
    u=require(request,SALES);data=await request.form();validate_csrf(request,data)
    description=val(data,'description',2000)
    if not description:return redirect(f'/clients/{client_id}?error=Descrição obrigatória')
    with db() as c:
        client_allowed(c,u,client_id);activity(c,u,client_id,'Contato',description)
        audit(c,u,'activities',client_id,'create',None,{'description':description})
    return redirect(f'/clients/{client_id}?success=Contato registrado')
@app.post('/clients/{client_id}/archive')
async def client_archive(request:Request,client_id:int):
    u=require(request,('admin','gerente'));data=await request.form();validate_csrf(request,data)
    with db() as c:
        old=record(client_allowed(c,u,client_id))
        c.execute('UPDATE clients SET archived=1,updated_at=? WHERE id=?',(iso_now(),client_id))
        audit(c,u,'clients',client_id,'archive',old,{'archived':1})
    return redirect('/clients?success=Cliente arquivado')

@app.get('/opportunities')
def opportunities(request:Request,q:str='',owner:int=0):
    u=require(request,SALES)
    filters=[];args=[]
    if q:filters.append('(cl.name LIKE ? OR o.product LIKE ?)');args.extend(('%'+q+'%',)*2)
    if u['role']=='vendedor':filters.append('o.owner_id=?');args.append(u['id'])
    elif owner:filters.append('o.owner_id=?');args.append(owner)
    where='WHERE '+' AND '.join(filters) if filters else ''
    with db() as c:
        rows=qall(c,f'SELECT o.*,cl.name client_name,cl.phone,u.name owner FROM opportunities o JOIN clients cl ON o.client_id=cl.id LEFT JOIN users u ON o.owner_id=u.id {where} ORDER BY o.updated_at DESC',args)
        users=qall(c,"SELECT id,name FROM users WHERE active=1 AND role IN ('admin','gerente','vendedor')")
    cols={s:[dict(r) for r in rows if r['stage']==s] for s in STAGES}
    return render(request,'opportunities.html',section='pipeline',columns=cols,q=q,owner=owner,users=users)

@app.get('/opportunities/new')
def opp_new(request:Request,client_id:int=0):
    u=require(request,SALES)
    with db() as c:
        cl=qall(c,'SELECT id,name FROM clients WHERE archived=0'+(' AND owner_id=?' if u['role']=='vendedor' else '')+' ORDER BY name',([u['id']] if u['role']=='vendedor' else []))
        users=qall(c,"SELECT id,name FROM users WHERE active=1 AND role IN ('admin','gerente','vendedor')")
    return render(request,'opportunity_form.html',section='pipeline',item={'client_id':client_id,'probability':30,'stage':STAGES[0]},clients=cl,users=users,is_edit=False)
@app.get('/opportunities/{oid}/edit')
def opp_edit(request:Request,oid:int):
    u=require(request,SALES)
    with db() as c:
        row=qone(c,'SELECT * FROM opportunities WHERE id=?',(oid,))
        if not row:raise HTTPException(404,'Oportunidade não encontrada')
        client_allowed(c,u,row['client_id'])
        cl=qall(c,'SELECT id,name FROM clients WHERE archived=0'+(' AND owner_id=?' if u['role']=='vendedor' else '')+' ORDER BY name',([u['id']] if u['role']=='vendedor' else []))
        users=qall(c,"SELECT id,name FROM users WHERE active=1 AND role IN ('admin','gerente','vendedor')")
    return render(request,'opportunity_form.html',section='pipeline',item=row,clients=cl,users=users,is_edit=True)

async def save_opportunity(request,oid=None):
    u=require(request,SALES);data=await request.form();validate_csrf(request,data)
    product=val(data,'product',255);client_id=intval(data.get('client_id'))
    if not product or not client_id:return redirect(request.url.path+'?error=Cliente e produto são obrigatórios')
    try:amount=money_to_cents(data.get('value'))
    except ValueError as exc:return redirect(request.url.path+'?error='+quote(str(exc)))
    stage=val(data,'stage',100)
    if stage not in STAGES:stage=STAGES[0]
    probability=max(0,min(100,intval(data.get('probability'),30)))
    with db() as c:
        client_allowed(c,u,client_id)
        old=None
        if oid:
            oldrow=qone(c,'SELECT * FROM opportunities WHERE id=?',(oid,))
            if not oldrow:raise HTTPException(404,'Oportunidade não encontrada')
            client_allowed(c,u,oldrow['client_id']);old=record(oldrow)
        values=dict(client_id=client_id,product=product,value_cents=amount,probability=probability,stage=stage,
          expected_close=clean_day(data.get('expected_close')),source=val(data,'source',80),
          owner_id=assignee(c,u,data.get('owner_id')),notes=val(data,'notes',3000),loss_reason=val(data,'loss_reason',100) if stage=='Venda perdida' else '')
        if oid:
            c.execute('UPDATE opportunities SET '+','.join(f'{k}=?' for k in values)+',updated_at=? WHERE id=?',(*values.values(),iso_now(),oid))
            audit(c,u,'opportunities',oid,'update',old,values)
            if old['stage']!=stage:activity(c,u,client_id,'Funil',f"Oportunidade #{oid}: {old['stage']} → {stage}")
        else:
            cur=c.execute('INSERT INTO opportunities ('+','.join(values)+') VALUES ('+','.join('?' for _ in values)+')',list(values.values()))
            oid=cur.lastrowid;audit(c,u,'opportunities',oid,'create',None,values)
            activity(c,u,client_id,'Funil',f'Oportunidade #{oid} criada para {product}')
    return redirect('/opportunities?success=Oportunidade salva')

@app.post('/opportunities/new')
async def opp_create(request:Request):return await save_opportunity(request)
@app.post('/opportunities/{oid}/edit')
async def opp_update(request:Request,oid:int):return await save_opportunity(request,oid)
@app.patch('/api/opportunities/{oid}/stage')
async def opp_stage(request:Request,oid:int):
    u=require(request,SALES)
    data=await request.json();validate_csrf(request,data)
    stage=data.get('stage')
    if stage not in STAGES:return JSONResponse({'error':'Etapa inválida'},status_code=400)
    with db() as c:
        old=qone(c,'SELECT * FROM opportunities WHERE id=?',(oid,))
        if not old:raise HTTPException(404,'Oportunidade não encontrada')
        client_allowed(c,u,old['client_id'])
        if stage=='Venda perdida':return JSONResponse({'error':'Para registrar uma perda, abra o card e informe o motivo.'},status_code=400)
        c.execute('UPDATE opportunities SET stage=?,updated_at=? WHERE id=?',(stage,iso_now(),oid))
        audit(c,u,'opportunities',oid,'stage_change',{'stage':old['stage']},{'stage':stage})
        activity(c,u,old['client_id'],'Funil',f"Oportunidade #{oid}: {old['stage']} → {stage}")
    return JSONResponse({'ok':True,'stage':stage})

@app.get('/followups')
def followups(request:Request,filter:str='todos',q:str=''):
    u=require(request,SALES)
    today=now().date();tomorrow=(today+timedelta(days=1)).isoformat();week=(today+timedelta(days=7)).isoformat()
    conditions=['1=1'];args=[]
    if u['role']=='vendedor':conditions.append('f.owner_id=?');args.append(u['id'])
    if q:conditions.append('(cl.name LIKE ? OR f.product LIKE ?)');args.extend(['%'+q+'%']*2)
    if filter=='hoje':conditions.extend(["f.status='Pendente'",'substr(f.due_at,1,10)=?']);args.append(today.isoformat())
    elif filter=='atrasados':conditions.extend(["f.status='Pendente'",'f.due_at<?']);args.append(iso_now())
    elif filter=='amanha':conditions.extend(["f.status='Pendente'",'substr(f.due_at,1,10)=?']);args.append(tomorrow)
    elif filter=='semana':conditions.extend(["f.status='Pendente'",'substr(f.due_at,1,10) BETWEEN ? AND ?']);args.extend([today.isoformat(),week])
    elif filter=='pendentes':conditions.append("f.status='Pendente'")
    elif filter=='concluidos':conditions.append("f.status='Concluído'")
    elif filter in ['Promoção','Estoque','Aguardar pagamento','Retorno futuro','Sem resposta']:
        conditions.append('f.category=?');args.append(filter)
    with db() as c:
        rows=qall(c,'SELECT f.*,cl.name client_name,cl.phone phone,u.name owner FROM followups f JOIN clients cl ON cl.id=f.client_id LEFT JOIN users u ON u.id=f.owner_id WHERE '+' AND '.join(conditions)+' ORDER BY CASE WHEN f.status=\'Pendente\' THEN 0 ELSE 1 END,f.due_at LIMIT 300',args)
    return render(request,'followups.html',section='followups',rows=rows,filter=filter,q=q,today=today.isoformat())

@app.get('/followups/new')
def followup_new(request:Request,client_id:int=0):
    u=require(request,SALES)
    with db() as c:
        cl=qall(c,'SELECT id,name FROM clients WHERE archived=0'+(' AND owner_id=?' if u['role']=='vendedor' else '')+' ORDER BY name',([u['id']] if u['role']=='vendedor' else []))
        users=qall(c,"SELECT id,name FROM users WHERE active=1 AND role IN ('admin','gerente','vendedor')")
    return render(request,'followup_form.html',section='followups',item={'client_id':client_id,'priority':'Média','status':'Pendente'},clients=cl,users=users,is_edit=False)
@app.get('/followups/{fid}/edit')
def followup_edit(request:Request,fid:int):
    u=require(request,SALES)
    with db() as c:
        item=qone(c,'SELECT * FROM followups WHERE id=?',(fid,))
        if not item:raise HTTPException(404,'Follow-up não encontrado')
        client_allowed(c,u,item['client_id'])
        cl=qall(c,'SELECT id,name FROM clients WHERE archived=0'+(' AND owner_id=?' if u['role']=='vendedor' else '')+' ORDER BY name',([u['id']] if u['role']=='vendedor' else []))
        users=qall(c,"SELECT id,name FROM users WHERE active=1 AND role IN ('admin','gerente','vendedor')")
    return render(request,'followup_form.html',section='followups',item=item,clients=cl,users=users,is_edit=True)

async def save_followup(request,fid=None):
    u=require(request,SALES);data=await request.form();validate_csrf(request,data)
    client_id=intval(data.get('client_id'));product=val(data,'product',255)
    try:due=clean_date(data.get('due_at'));quoted=money_to_cents(data.get('quoted_value'))
    except ValueError as exc:return redirect(request.url.path+'?error='+quote(str(exc)))
    if not due or not product or not client_id:return redirect(request.url.path+'?error=Preencha cliente, produto e prazo')
    with db() as c:
        client_allowed(c,u,client_id);old=None
        if fid:
            oldrow=qone(c,'SELECT * FROM followups WHERE id=?',(fid,))
            if not oldrow:raise HTTPException(404,'Follow-up não encontrado')
            client_allowed(c,u,oldrow['client_id']);old=record(oldrow)
        values=dict(client_id=client_id,product=product,quoted_value_cents=quoted,objection=val(data,'objection',500),
            due_at=due,priority=val(data,'priority',60) if val(data,'priority',60) in PRIORITIES else 'Média',
            owner_id=assignee(c,u,data.get('owner_id')),status=val(data,'status',30) if val(data,'status',30) in ('Pendente','Concluído') else 'Pendente',
            notes=val(data,'notes',3000),category=val(data,'category',100),recurring=val(data,'recurring',80))
        values['completed_at']=iso_now() if values['status']=='Concluído' else None
        if fid:
            c.execute('UPDATE followups SET '+','.join(f'{k}=?' for k in values)+',updated_at=? WHERE id=?',(*values.values(),iso_now(),fid))
            audit(c,u,'followups',fid,'update',old,values)
        else:
            cur=c.execute('INSERT INTO followups ('+','.join(values)+') VALUES ('+','.join('?' for _ in values)+')',list(values.values()))
            fid=cur.lastrowid;audit(c,u,'followups',fid,'create',None,values)
            activity(c,u,client_id,'Follow-up',f'Agendado retorno sobre {product} para {due}')
    return redirect('/followups?success=Follow-up salvo')

@app.post('/followups/new')
async def followup_create(request:Request):return await save_followup(request)
@app.post('/followups/{fid}/edit')
async def followup_update(request:Request,fid:int):return await save_followup(request,fid)
@app.post('/followups/{fid}/complete')
async def followup_complete(request:Request,fid:int):
    u=require(request,SALES);data=await request.form();validate_csrf(request,data)
    with db() as c:
        old=qone(c,'SELECT * FROM followups WHERE id=?',(fid,))
        if not old:raise HTTPException(404,'Follow-up não encontrado')
        client_allowed(c,u,old['client_id'])
        c.execute("UPDATE followups SET status='Concluído',completed_at=?,updated_at=? WHERE id=?",(iso_now(),iso_now(),fid))
        audit(c,u,'followups',fid,'complete',{'status':old['status']},{'status':'Concluído'})
        activity(c,u,old['client_id'],'Follow-up',f'Retorno sobre {old["product"]} concluído')
    return redirect('/followups?success=Follow-up concluído')

@app.get('/orders')
def orders(request:Request,q:str='',status:str='',page:int=1):
    u=require(request,OPS)
    page=max(1,page);where=['1=1'];args=[]
    if q:where.append('(o.order_number LIKE ? OR cl.name LIKE ? OR o.tracking_code LIKE ?)');args.extend(['%'+q+'%']*3)
    if status:where.append('o.operational_status=?');args.append(status)
    if u['role']=='vendedor':where.append('o.owner_id=?');args.append(u['id'])
    with db() as c:
        count=qone(c,'SELECT COUNT(*) n FROM orders o JOIN clients cl ON o.client_id=cl.id WHERE '+' AND '.join(where),args)['n']
        rows=qall(c,'SELECT o.*,cl.name client_name FROM orders o JOIN clients cl ON o.client_id=cl.id WHERE '+' AND '.join(where)+' ORDER BY o.created_at DESC LIMIT 25 OFFSET ?',(*args,(page-1)*25))
    return render(request,'orders.html',section='orders',rows=rows,total=count,page=page,q=q,status=status)
@app.get('/orders/new')
def order_new(request:Request,client_id:int=0):
    u=require(request,OPS)
    with db() as c:
        cl=qall(c,'SELECT id,name FROM clients WHERE archived=0'+(' AND owner_id=?' if u['role']=='vendedor' else '')+' ORDER BY name',([u['id']] if u['role']=='vendedor' else []))
        users=qall(c,'SELECT id,name FROM users WHERE active=1 ORDER BY name')
        products=qall(c,'SELECT name FROM products WHERE active=1 ORDER BY name')
    return render(request,'order_form.html',section='orders',item={'client_id':client_id,'payment_status':'Pendente','operational_status':'Pedido recebido','shipping_status':'Não enviado'},clients=cl,users=users,is_edit=False,items=[],products=products)
@app.get('/orders/{oid}')
def order_detail(request:Request,oid:int):
    u=require(request,OPS)
    with db() as c:
        item=qone(c,'SELECT o.*,cl.name client_name,cl.phone,cl.email FROM orders o JOIN clients cl ON o.client_id=cl.id WHERE o.id=?',(oid,))
        if not item:raise HTTPException(404,'Pedido não encontrado')
        if u['role']=='vendedor' and item['owner_id']!=u['id']:raise HTTPException(403,'Pedido atribuído a outro vendedor')
        products=qall(c,'SELECT * FROM order_items WHERE order_id=?',(oid,))
        logs=qall(c,"SELECT a.*,u.name author FROM audit_logs a LEFT JOIN users u ON u.id=a.user_id WHERE a.entity='orders' AND a.entity_id=? ORDER BY a.created_at DESC LIMIT 30",(oid,))
    return render(request,'order_detail.html',section='orders',item=item,products=products,logs=logs,ctl=order_control(dict(item)),next_url=f'/orders/{oid}')
@app.get('/orders/{oid}/edit')
def order_edit(request:Request,oid:int):
    u=require(request,OPS)
    with db() as c:
        item=qone(c,'SELECT * FROM orders WHERE id=?',(oid,))
        if not item:raise HTTPException(404,'Pedido não encontrado')
        if u['role']=='vendedor' and item['owner_id']!=u['id']:raise HTTPException(403,'Pedido atribuído a outro vendedor')
        cl=qall(c,'SELECT id,name FROM clients WHERE archived=0'+(' AND owner_id=?' if u['role']=='vendedor' else '')+' ORDER BY name',([u['id']] if u['role']=='vendedor' else []))
        users=qall(c,'SELECT id,name FROM users WHERE active=1 ORDER BY name')
        items=qall(c,'SELECT * FROM order_items WHERE order_id=?',(oid,))
        products=qall(c,'SELECT name FROM products WHERE active=1 ORDER BY name')
    return render(request,'order_form.html',section='orders',item=item,clients=cl,users=users,is_edit=True,items=items,products=products)

MAX_ORDER_ITEMS=50

def parse_order_items(data):
    """Read repeated product/sku/quantity/unit_price fields. Blank rows are ignored."""
    products=data.getlist('product');skus=data.getlist('sku');qtys=data.getlist('quantity');units=data.getlist('unit_price')
    items=[]
    for n,name in enumerate(products):
        name=str(name or '').strip()[:255]
        if not name:continue
        qty=intval(qtys[n] if n<len(qtys) else 1,1)
        items.append(dict(product=name,sku=str(skus[n] if n<len(skus) else '').strip()[:100],
                          quantity=max(1,min(qty,100000)),unit_cents=money_to_cents(units[n] if n<len(units) else '0')))
    if len(items)>MAX_ORDER_ITEMS:raise ValueError(f'Máximo de {MAX_ORDER_ITEMS} itens por pedido.')
    return items

async def save_order(request,oid=None):
    u=require(request,OPS);data=await request.form();validate_csrf(request,data)
    client_id=intval(data.get('client_id'));number=val(data,'order_number',80)
    if not client_id or not number:return redirect(request.url.path+'?error=Cliente e número do pedido são obrigatórios')
    try:
        total=money_to_cents(data.get('total'));shipcost=money_to_cents(data.get('shipping'));discount=money_to_cents(data.get('discount'))
        order_items=parse_order_items(data)
    except ValueError as exc:return redirect(request.url.path+'?error='+quote(str(exc)))
    if not total and order_items:
        # Total left blank: derive from the items (subtotal - discount + shipping).
        total=max(0,sum(i['quantity']*i['unit_cents'] for i in order_items)-discount+shipcost)
    with db() as c:
        client_allowed(c,u,client_id) if u['role']=='vendedor' else None
        if not qone(c,'SELECT 1 FROM clients WHERE id=? AND archived=0',(client_id,)):
            return redirect(request.url.path+'?error=Cliente inválido')
        old=None
        if oid:
            row=qone(c,'SELECT * FROM orders WHERE id=?',(oid,))
            if not row:raise HTTPException(404,'Pedido não encontrado')
            if u['role']=='vendedor' and row['owner_id']!=u['id']:raise HTTPException(403,'Pedido atribuído a outro vendedor')
            old=record(row)
        if qone(c,'SELECT id FROM orders WHERE order_number=? AND id!=?',(number,oid or -1)):
            return redirect(request.url.path+'?error=Número do pedido já existe')
        values=dict(client_id=client_id,order_number=number,total_cents=total,shipping_cents=shipcost,discount_cents=discount,
          payment_status=val(data,'payment_status',60) if val(data,'payment_status',60) in PAYMENTS else 'Pendente',
          operational_status=val(data,'operational_status',100) if val(data,'operational_status',100) in OPERATIONS else OPERATIONS[0],
          shipping_status=val(data,'shipping_status',100) if val(data,'shipping_status',100) in SHIPPING else SHIPPING[0],
          carrier=val(data,'carrier',100),tracking_code=val(data,'tracking_code',100),
          destination_address=val(data,'destination_address',500),due_at=clean_date(data.get('due_at')),
          owner_id=assignee(c,u,data.get('owner_id')),notes=val(data,'notes',3000))
        if oid:
            c.execute('UPDATE orders SET '+','.join(f'{k}=?' for k in values)+',updated_at=? WHERE id=?',(*values.values(),iso_now(),oid))
            audit(c,u,'orders',oid,'update',old,values)
        else:
            values['source']='Manual'
            cur=c.execute('INSERT INTO orders ('+','.join(values)+') VALUES ('+','.join('?' for _ in values)+')',list(values.values()))
            oid=cur.lastrowid;audit(c,u,'orders',oid,'create',None,values)
            activity(c,u,client_id,'Pedido',f'Pedido manual {number} cadastrado')
        # Item rows are managed in this form, transactionally. No stock movements.
        before_items=[dict(product=r['product'],sku=r['sku'],quantity=r['quantity'],unit_cents=r['unit_cents'])
                      for r in qall(c,'SELECT product,sku,quantity,unit_cents FROM order_items WHERE order_id=? ORDER BY id',(oid,))]
        c.execute('DELETE FROM order_items WHERE order_id=?',(oid,))
        for it in order_items:
            c.execute('INSERT INTO order_items (order_id,product,sku,quantity,unit_cents) VALUES (?,?,?,?,?)',
                      (oid,it['product'],it['sku'],it['quantity'],it['unit_cents']))
        if before_items!=order_items:
            audit(c,u,'orders',oid,'items',before_items or None,order_items or None)
    return redirect(f'/orders/{oid}?success=Pedido salvo')

@app.post('/orders/new')
async def order_create(request:Request):return await save_order(request)
@app.post('/orders/{oid}/edit')
async def order_update(request:Request,oid:int):return await save_order(request,oid)

def add_months(d,k):
    month0=d.month-1+k;year=d.year+month0//12;month=month0%12+1;day=d.day
    while True:
        try:return d.replace(year=year,month=month,day=day)
        except ValueError:day-=1

def next_occurrence(due_at,recurrence):
    """Próxima data da tarefa recorrente, sempre no futuro; mensal preserva o dia original quando possível."""
    base=parse_dt(due_at) or now()
    days={'Diária':1,'Semanal':7,'Quinzenal':14}.get(recurrence)
    for k in range(1,1200):
        nxt=base+timedelta(days=days*k) if days else add_months(base,k)
        if nxt>now():break
    return nxt.replace(tzinfo=None).isoformat(timespec='minutes')

@app.get('/tasks')
def tasks(request:Request,filter:str='pendentes'):
    u=require(request)
    clauses=['1=1'];args=[]
    if filter=='pendentes':clauses.append("t.status='Pendente'")
    elif filter=='concluidas':clauses.append("t.status='Concluída'")
    if u['role'] not in ('admin','gerente'):clauses.append('t.assignee_id=?');args.append(u['id'])
    with db() as c:
        rows=qall(c,'SELECT t.*,u.name assignee,cl.name client_name FROM tasks t LEFT JOIN users u ON t.assignee_id=u.id LEFT JOIN clients cl ON cl.id=t.client_id WHERE '+' AND '.join(clauses)+' ORDER BY CASE WHEN t.status=\'Pendente\' THEN 0 ELSE 1 END,t.due_at LIMIT 200',args)
    return render(request,'tasks.html',section='tasks',rows=rows,filter=filter)
@app.get('/tasks/new')
def task_new(request:Request,client_id:int=0,order_id:int=0):
    u=require(request)
    with db() as c:
        users=qall(c,'SELECT id,name FROM users WHERE active=1 ORDER BY name')
        cl=qall(c,'SELECT id,name FROM clients WHERE archived=0 ORDER BY name LIMIT 500') if u['role'] in SALES else []
        orders=qall(c,'SELECT id,order_number FROM orders ORDER BY created_at DESC LIMIT 100') if u['role'] in OPS else []
    return render(request,'task_form.html',section='tasks',users=users,clients=cl,orders=orders,item={'client_id':client_id,'order_id':order_id,'priority':'Média'})
@app.post('/tasks/new')
async def task_create(request:Request):
    u=require(request);data=await request.form();validate_csrf(request,data)
    title=val(data,'title',180)
    if not title:return redirect('/tasks/new?error=Título obrigatório')
    with db() as c:
        client_id=intval(data.get('client_id')) or None
        order_id=intval(data.get('order_id')) or None
        if client_id and u['role'] not in SALES:raise HTTPException(403,'Sem acesso a clientes')
        if client_id and u['role']=='vendedor':client_allowed(c,u,client_id)
        if order_id and u['role'] not in OPS:raise HTTPException(403,'Sem acesso a pedidos')
        if order_id and u['role']=='vendedor':
            o=qone(c,'SELECT owner_id FROM orders WHERE id=?',(order_id,))
            if not o or o['owner_id']!=u['id']:raise HTTPException(403,'Sem acesso ao pedido')
        values=dict(title=title,description=val(data,'description',2000),due_at=clean_date(data.get('due_at')),
            priority=val(data,'priority',50) if val(data,'priority',50) in PRIORITIES else 'Média',
            assignee_id=assignee(c,u,data.get('assignee_id')) if u['role'] in ('admin','gerente','vendedor') else u['id'],
            client_id=client_id,order_id=order_id,recurrence=val(data,'recurrence',20) if val(data,'recurrence',20) in RECURRENCES else '')
        cur=c.execute('INSERT INTO tasks ('+','.join(values)+') VALUES ('+','.join('?' for _ in values)+')',list(values.values()))
        audit(c,u,'tasks',cur.lastrowid,'create',None,values)
    return redirect('/tasks?success=Tarefa criada')
@app.post('/tasks/{tid}/toggle')
async def task_toggle(request:Request,tid:int):
    u=require(request);data=await request.form();validate_csrf(request,data)
    with db() as c:
        old=qone(c,'SELECT * FROM tasks WHERE id=?',(tid,))
        if not old:raise HTTPException(404,'Tarefa não encontrada')
        if u['role'] not in ('admin','gerente') and old['assignee_id']!=u['id']:
            raise HTTPException(403,'Tarefa atribuída a outro usuário')
        new='Concluída' if old['status']=='Pendente' else 'Pendente'
        c.execute('UPDATE tasks SET status=?,updated_at=? WHERE id=?',(new,iso_now(),tid))
        audit(c,u,'tasks',tid,'status_change',{'status':old['status']},{'status':new})
        if new=='Concluída' and old['recurrence']:
            nxt=next_occurrence(old['due_at'],old['recurrence'])
            cur=c.execute('INSERT INTO tasks (title,description,due_at,priority,assignee_id,client_id,order_id,recurrence) VALUES (?,?,?,?,?,?,?,?)',
                (old['title'],old['description'],nxt,old['priority'],old['assignee_id'],old['client_id'],old['order_id'],old['recurrence']))
            audit(c,u,'tasks',cur.lastrowid,'recurring_create',None,{'due_at':nxt,'recurrence':old['recurrence']})
    return redirect('/tasks?success=Tarefa atualizada')


# ------------------------- PRODUÇÃO -------------------------
# Vendidos = itens de pedidos com pagamento Aprovado e não cancelados/devolvidos, ligados ao produto por nome ou SKU.
SOLD_SQL="""(SELECT COALESCE(SUM(oi.quantity),0) FROM order_items oi JOIN orders o ON o.id=oi.order_id
  WHERE o.payment_status='Aprovado' AND o.operational_status!='Cancelado/devolvido'
  AND (LOWER(TRIM(oi.product))=LOWER(TRIM(p.name)) OR (IFNULL(p.sku,'')!='' AND oi.sku=p.sku)))"""

def production_summary(c,product_id=None):
    extra=' AND p.id=?' if product_id else ''
    rows=qall(c,f"""SELECT p.id,p.name,p.sku,
      COALESCE((SELECT SUM(b.qty_finished) FROM production_batches b WHERE b.product_id=p.id),0) finished,
      COALESCE((SELECT SUM(b.qty_planned-b.qty_finished) FROM production_batches b WHERE b.product_id=p.id AND b.status IN ('Planejado','Em produção')),0) in_progress,
      {SOLD_SQL} sold FROM products p WHERE p.active=1{extra} ORDER BY p.name""",([product_id] if product_id else []))
    items=[{**dict(r),'stock':r['finished']-r['sold']} for r in rows]
    totals={k:sum(i[k] for i in items) for k in ('in_progress','finished','sold','stock')}
    return items,totals

def batch_query(where='',args=()):
    return ('SELECT b.*,p.name product,uo.name owner FROM production_batches b JOIN products p ON p.id=b.product_id '
            'LEFT JOIN users uo ON uo.id=b.owner_id '+where,list(args))

@app.get('/production')
def production(request:Request,status:str=''):
    u=require(request,PROD_VIEW)
    with db() as c:
        products,totals=production_summary(c)
        sql,args=batch_query('WHERE b.status=? ' if status in BATCH_STATUS else '',[status] if status in BATCH_STATUS else [])
        batches=qall(c,sql+"ORDER BY CASE WHEN b.status IN ('Planejado','Em produção') THEN 0 ELSE 1 END,COALESCE(b.due_at,'9999'),b.id DESC LIMIT 200",args)
    return render(request,'production.html',section='production',title='Produção',products=products,totals=totals,batches=batches,status=status,statuses=BATCH_STATUS,can_edit=u['role'] in PROD_EDIT)

def production_form_data(c):
    return (qall(c,'SELECT id,name FROM products WHERE active=1 ORDER BY name'),
            qall(c,"SELECT id,name FROM users WHERE active=1 ORDER BY name"))

@app.get('/production/new')
def production_new(request:Request):
    u=require(request,PROD_EDIT)
    with db() as c:products,users=production_form_data(c)
    return render(request,'production_form.html',section='production',title='Produção',item={'status':'Planejado'},products=products,users=users,is_edit=False)

@app.get('/production/{bid}/edit')
def production_edit(request:Request,bid:int):
    u=require(request,PROD_EDIT)
    with db() as c:
        item=qone(c,'SELECT * FROM production_batches WHERE id=?',(bid,))
        if not item:raise HTTPException(404,'Lote não encontrado')
        products,users=production_form_data(c)
    return render(request,'production_form.html',section='production',title='Produção',item=item,products=products,users=users,is_edit=True)

async def save_batch(request,bid=None):
    u=require(request,PROD_EDIT);data=await request.form();validate_csrf(request,data)
    back=request.url.path
    qty=intval(data.get('qty_planned'),0)
    if qty<1 or qty>100000:return redirect(back+'?error='+quote('Informe a quantidade planejada (1 a 100.000).'))
    try:due=clean_date(data.get('due_at'))
    except ValueError as exc:return redirect(back+'?error='+quote(str(exc)))
    with db() as c:
        old=None
        if bid:
            row=qone(c,'SELECT * FROM production_batches WHERE id=?',(bid,))
            if not row:raise HTTPException(404,'Lote não encontrado')
            if qty<row['qty_finished']:return redirect(back+'?error='+quote(f"O lote já tem {row['qty_finished']} unidade(s) finalizada(s); a quantidade planejada não pode ser menor."))
            old=record(row)
        new_name=val(data,'new_product',255)
        if new_name:
            found=qone(c,'SELECT id FROM products WHERE name=?',(new_name,))
            if found:product_id=found['id']
            else:product_id=c.execute('INSERT INTO products (name,sku) VALUES (?,?)',(new_name,val(data,'new_sku',100))).lastrowid
        else:
            product_id=intval(data.get('product_id'))
            if not qone(c,'SELECT 1 FROM products WHERE id=? AND active=1',(product_id,)):
                return redirect(back+'?error='+quote('Selecione ou cadastre um produto.'))
        code=val(data,'code',40)
        if not code:
            n=qone(c,'SELECT COUNT(*) n FROM production_batches')['n']+1
            while True:
                code=f'LT-{now().year}-{n:03d}'
                if not qone(c,'SELECT 1 FROM production_batches WHERE code=?',(code,)):break
                n+=1
        elif qone(c,'SELECT 1 FROM production_batches WHERE code=? AND id!=?',(code,bid or -1)):
            return redirect(back+'?error='+quote('Já existe um lote com esse código.'))
        owner=assignee(c,u,data.get('owner_id'))
        values=dict(code=code,product_id=product_id,qty_planned=qty,due_at=due,owner_id=owner,notes=val(data,'notes',3000))
        if bid:
            status=old['status']
            if status=='Finalizado' and qty>old['qty_finished']:status,values['finished_at']='Em produção',None
            elif status=='Em produção' and qty==old['qty_finished']:status,values['finished_at']='Finalizado',iso_now()
            values['status']=status
            c.execute('UPDATE production_batches SET '+','.join(f'{k}=?' for k in values)+',updated_at=? WHERE id=?',(*values.values(),iso_now(),bid))
            audit(c,u,'production_batches',bid,'update',old,values)
        else:
            cur=c.execute('INSERT INTO production_batches ('+','.join(values)+') VALUES ('+','.join('?' for _ in values)+')',list(values.values()))
            bid=cur.lastrowid;audit(c,u,'production_batches',bid,'create',None,values)
    return redirect(f'/production/{bid}?success='+quote('Lote salvo'))

@app.post('/production/new')
async def production_create(request:Request):return await save_batch(request)
@app.post('/production/{bid}/edit')
async def production_update(request:Request,bid:int):return await save_batch(request,bid)

@app.get('/production/{bid}')
def production_detail(request:Request,bid:int):
    u=require(request,PROD_VIEW)
    with db() as c:
        sql,args=batch_query('WHERE b.id=?',[bid]);item=qone(c,sql,args)
        if not item:raise HTTPException(404,'Lote não encontrado')
        logs=qall(c,'SELECT l.*,us.name author FROM production_logs l LEFT JOIN users us ON us.id=l.user_id WHERE l.batch_id=? ORDER BY l.id DESC LIMIT 100',(bid,))
        prod,_=production_summary(c,item['product_id'])
    return render(request,'production_detail.html',section='production',title='Produção',item=item,logs=logs,prod=prod[0] if prod else None,can_edit=u['role'] in PROD_EDIT)

@app.post('/production/{bid}/finish')
async def production_finish(request:Request,bid:int):
    u=require(request,PROD_EDIT);data=await request.form();validate_csrf(request,data)
    qty=intval(data.get('qty'),0);back=f'/production/{bid}'
    if qty==0:return redirect(back+'?error='+quote('Informe uma quantidade diferente de zero.'))
    with db() as c:
        b=qone(c,'SELECT * FROM production_batches WHERE id=?',(bid,))
        if not b:raise HTTPException(404,'Lote não encontrado')
        if b['status']=='Cancelado':return redirect(back+'?error='+quote('Lote cancelado: reabra o lote antes de apontar produção.'))
        new=b['qty_finished']+qty
        if new<0 or new>b['qty_planned']:
            return redirect(back+'?error='+quote(f"Quantidade inválida: o lote tem {b['qty_finished']} de {b['qty_planned']} unidade(s) finalizada(s)."))
        status='Finalizado' if new==b['qty_planned'] else 'Em produção'
        c.execute('UPDATE production_batches SET qty_finished=?,status=?,started_at=COALESCE(started_at,?),finished_at=?,updated_at=? WHERE id=?',
                  (new,status,iso_now(),iso_now() if status=='Finalizado' else None,iso_now(),bid))
        c.execute('INSERT INTO production_logs (batch_id,qty,note,user_id) VALUES (?,?,?,?)',(bid,qty,val(data,'note',300),u['id']))
        audit(c,u,'production_batches',bid,'finish',{'qty_finished':b['qty_finished'],'status':b['status']},{'qty_finished':new,'status':status})
    return redirect(back+'?success='+quote('Produção apontada'))

@app.post('/production/{bid}/status')
async def production_status(request:Request,bid:int):
    u=require(request,PROD_EDIT);data=await request.form();validate_csrf(request,data)
    target=val(data,'status',30);back=f'/production/{bid}'
    if target not in ('Planejado','Em produção','Cancelado'):raise HTTPException(400,'Status inválido')
    with db() as c:
        b=qone(c,'SELECT * FROM production_batches WHERE id=?',(bid,))
        if not b:raise HTTPException(404,'Lote não encontrado')
        if b['status']=='Finalizado':return redirect(back+'?error='+quote('Lote finalizado: para corrigir, use o apontamento com quantidade negativa.'))
        if target=='Planejado' and b['qty_finished']>0:return redirect(back+'?error='+quote('O lote já tem unidades finalizadas; não pode voltar para Planejado.'))
        c.execute('UPDATE production_batches SET status=?,started_at=CASE WHEN ?=\'Em produção\' THEN COALESCE(started_at,?) ELSE started_at END,updated_at=? WHERE id=?',(target,target,iso_now(),iso_now(),bid))
        audit(c,u,'production_batches',bid,'status',{'status':b['status']},{'status':target})
    return redirect(back+'?success='+quote('Status atualizado'))

@app.get('/integrations')
def integrations(request:Request):
    u=require(request,('admin','gerente'))
    with db() as c: rows=qall(c,'SELECT * FROM integrations ORDER BY service')
    return render(request,'integrations.html',section='integrations',rows=rows)

@app.get('/reports')
def reports(request:Request):
    u=require(request,('admin','gerente','financeiro'))
    with db() as c:
        totals=qone(c,"SELECT COUNT(*) count,COALESCE(SUM(total_cents),0) total FROM orders WHERE payment_status='Aprovado' AND operational_status!='Cancelado/devolvido'")
        by_payment=qall(c,'SELECT payment_status,COUNT(*) count,COALESCE(SUM(total_cents),0) total FROM orders GROUP BY payment_status')
    return render(request,'reports.html',section='reports',totals=totals,by_payment=by_payment)

@app.get('/export/{entity}')
def export(request:Request,entity:str):
    u=require(request,('admin','gerente','financeiro'))
    sqls={
      'orders':'SELECT order_number,source,total_cents,payment_status,operational_status,shipping_status,created_at FROM orders ORDER BY created_at DESC',
      'clients':'SELECT name,phone,email,city,state,vehicle_model,origin,created_at FROM clients WHERE archived=0 ORDER BY created_at DESC',
      'followups':'SELECT id,client_id,product,quoted_value_cents,due_at,status,priority,category FROM followups ORDER BY due_at DESC'
    }
    if entity not in sqls:raise HTTPException(404,'Exportação não disponível')
    if entity=='clients' and u['role']=='financeiro':raise HTTPException(403,'Sem acesso à lista de clientes')
    with db() as c:
        cur=c.execute(sqls[entity]);headers=[d[0] for d in cur.description];rows=cur.fetchall()
    buffer=io.StringIO();buffer.write('\ufeff');writer=csv.writer(buffer,delimiter=';');writer.writerow(headers)
    # Prevent CSV formula injection when opening exports in spreadsheets.
    def safe_csv(v):
        if isinstance(v,str) and v.lstrip().startswith(('=','+','-','@','\t','\r')): return "'"+v
        return v
    for r in rows:writer.writerow([safe_csv(v) for v in r])
    return StreamingResponse(iter([buffer.getvalue()]),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':f'attachment; filename="lancer_{entity}.csv"'})

@app.get('/team')
def team(request:Request):
    u=require(request,('admin','gerente'))
    with db() as c: rows=qall(c,'SELECT id,name,email,role,active,created_at FROM users ORDER BY created_at DESC')
    return render(request,'team.html',section='team',rows=rows)
@app.post('/team')
async def team_create(request:Request):
    u=require(request,('admin',));data=await request.form();validate_csrf(request,data)
    email=val(data,'email',250).lower();name=val(data,'name',150);role=val(data,'role',40);pw=val(data,'password',255)
    if len(name)<2 or '@' not in email or role not in ROLES or len(pw)<12:
        return redirect('/team?error=Nome, e-mail, perfil e senha com 12 ou mais caracteres são obrigatórios')
    with db() as c:
        if qone(c,'SELECT id FROM users WHERE email=?',(email,)):return redirect('/team?error=E-mail já cadastrado')
        cur=c.execute('INSERT INTO users (name,email,password_hash,role) VALUES (?,?,?,?)',(name,email,hash_password(pw),role))
        audit(c,u,'users',cur.lastrowid,'create',None,{'name':name,'email':email,'role':role})
    return redirect('/team?success=Usuário criado')

@app.get('/settings')
def settings(request:Request):
    u=require(request,('admin','gerente'))
    return render(request,'settings.html',section='settings',db_location='instance/lancer_parts.db')

@app.post('/settings')
async def settings_save(request:Request):
    u=require(request,('admin',));data=await request.form();validate_csrf(request,data)
    raw=val(data,'store_whatsapp',40)
    number=wa_number(raw) if raw else ''
    if raw and not number:return redirect('/settings?error='+quote('Informe um número válido com DDD.'))
    with db() as c:
        old=get_setting(c,'store_whatsapp')
        c.execute('INSERT INTO app_settings (key,value,updated_at) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at',('store_whatsapp',number,iso_now()))
        audit(c,u,'settings',0,'update',{'store_whatsapp':old} if old else None,{'store_whatsapp':number} if number else None)
    return redirect('/settings?success='+quote('WhatsApp da loja salvo'))

MODULES={
 'whatsapp':('WhatsApp / Atendimento','Conectar a API oficial da Meta ou provedor aprovado, com webhooks assinados, caixa de entrada e atribuição.'),
 'logistics':('Logística / Rastreamento','Conectar rastreamento oficial, eventos de transportadoras e fluxo operacional de expedição.'),
 'stock':('Estoque e Produtos','Produtos, SKUs, reservas, movimentos e sincronização a partir da Nuvemshop.'),
 'finance':('Financeiro','Margens apenas com custos reais, contas a pagar e a receber, taxas e conciliação.'),
 'marketing':('Marketing / Campanhas','Campanhas, listas segmentadas e mensuração com consentimento de contato.'),
 'after-sales':('Pós-venda','Rotinas de satisfação, garantia, devoluções e relacionamento pós-entrega.'),
 'insights':('Insights Lancer Parts','Insights baseados em métricas verificáveis do banco, sem sugerir números fictícios.')
}
@app.get('/modules/{slug}')
def module_page(request:Request,slug:str):
    require(request)
    if slug not in MODULES:raise HTTPException(404,'Módulo não encontrado')
    title,description=MODULES[slug]
    return render(request,'module.html',section=slug,title=title,description=description)

@app.get('/search')
def search(request:Request,q:str=''):
    u=require(request)
    query=q.strip()[:100]
    clients=[];orders=[];opps=[];tasks=[]
    if query:
        with db() as c:
            if u['role'] in SALES:
                clients=qall(c,'SELECT id,name,phone FROM clients WHERE archived=0 AND (name LIKE ? OR phone_normalized LIKE ?)'+(' AND owner_id=?' if u['role']=='vendedor' else '')+' LIMIT 15',('%'+query+'%','%'+phone_key(query)+'%',*([u['id']] if u['role']=='vendedor' else [])))
                opps=qall(c,'SELECT id,product,stage FROM opportunities WHERE product LIKE ?'+(' AND owner_id=?' if u['role']=='vendedor' else '')+' LIMIT 12',('%'+query+'%',*([u['id']] if u['role']=='vendedor' else [])))
            if u['role'] in OPS:
                orders=qall(c,'SELECT id,order_number,operational_status FROM orders WHERE (order_number LIKE ? OR tracking_code LIKE ?)'+(' AND owner_id=?' if u['role']=='vendedor' else '')+' LIMIT 15',('%'+query+'%','%'+query+'%',*([u['id']] if u['role']=='vendedor' else [])))
            tasks=qall(c,'SELECT id,title,status FROM tasks WHERE title LIKE ?'+(' AND assignee_id=?' if u['role'] not in ('admin','gerente') else '')+' LIMIT 15',('%'+query+'%',*([u['id']] if u['role'] not in ('admin','gerente') else [])))
    return render(request,'search.html',section='',q=query,clients=clients,orders=orders,opps=opps,tasks=tasks)

# Explicit, audited local bootstrap. Never ships with a default account/password.
def bootstrap(name,email,password):
    if len(password)<12:raise ValueError('Senha inicial deve ter no mínimo 12 caracteres')
    with db() as c:
        if qone(c,'SELECT 1 FROM users LIMIT 1'):raise ValueError('Já existem usuários. Adicione outros pelo painel Equipe.')
        c.execute('INSERT INTO users (name,email,password_hash,role) VALUES (?,?,?,?)',(name,email.lower(),hash_password(password),'admin'))
    print('Administrador criado. Proteja a senha informada e configure HTTPS antes de publicar.')

if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='bootstrap':
        if len(sys.argv)<4:
            print('Uso: python main.py bootstrap "Nome" email@exemplo.com (senha será solicitada sem eco)')
            sys.exit(2)
        import getpass
        bootstrap(sys.argv[2],sys.argv[3],getpass.getpass('Senha inicial (mín. 12 caracteres): '))
    else:
        print('Servidor: uvicorn main:app --reload --host 127.0.0.1 --port 8000')
