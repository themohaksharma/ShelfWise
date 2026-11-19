#!/usr/bin/env python3
"""ShelfWise single-origin web and API server (standard-library prototype)."""
from __future__ import annotations
import hashlib, json, math, mimetypes, os, secrets, sqlite3, time, uuid
from datetime import date, timedelta
from http import cookies
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get('SHELFWISE_DB_PATH', str(ROOT / 'shelfwise.sqlite3'))).expanduser().resolve()
PORT = int(os.environ.get('PORT', '10000'))
APP_ENV = os.environ.get('APP_ENV', 'development').strip().lower()
HOST = os.environ.get('SHELFWISE_HOST', '0.0.0.0' if APP_ENV == 'production' else '127.0.0.1')
SESSION_COOKIE_SECURE = os.environ.get('SESSION_COOKIE_SECURE', 'true' if APP_ENV == 'production' else 'false').strip().lower() in ('1', 'true', 'yes', 'on')
DEMO_ID = 'demo-shop'
SESSION_COOKIE = 'shelfwise_session'
SESSION_SECONDS = 60 * 60 * 24 * 14


def db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=15, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    c.execute('PRAGMA busy_timeout=15000')
    return c


def init_db():
    with db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS users(user_id TEXT PRIMARY KEY, identifier TEXT NOT NULL UNIQUE, salt BLOB NOT NULL, password_hash BLOB NOT NULL, created_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS shops(shop_id TEXT PRIMARY KEY, owner_user_id TEXT REFERENCES users(user_id) ON DELETE CASCADE, name TEXT NOT NULL, owner_name TEXT NOT NULL, category TEXT NOT NULL, address TEXT NOT NULL DEFAULT '', currency TEXT NOT NULL DEFAULT 'INR', revision INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE, expires_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS profile_photos(user_id TEXT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE, mime TEXT NOT NULL, image BLOB NOT NULL);
        CREATE TABLE IF NOT EXISTS competitor_prices(price_id TEXT NOT NULL,shop_id TEXT NOT NULL REFERENCES shops(shop_id) ON DELETE CASCADE,product_id TEXT NOT NULL,competitor TEXT NOT NULL,locality TEXT NOT NULL DEFAULT '',price REAL NOT NULL,checked_on TEXT NOT NULL,source_note TEXT NOT NULL DEFAULT '',PRIMARY KEY(shop_id,price_id));
        CREATE TABLE IF NOT EXISTS price_history(change_id TEXT NOT NULL,shop_id TEXT NOT NULL REFERENCES shops(shop_id) ON DELETE CASCADE,product_id TEXT NOT NULL,old_price REAL NOT NULL,new_price REAL NOT NULL,approved_by TEXT NOT NULL,approved_at TEXT NOT NULL,PRIMARY KEY(shop_id,change_id));
        CREATE TABLE IF NOT EXISTS seasonal_events(event_id TEXT NOT NULL,shop_id TEXT NOT NULL REFERENCES shops(shop_id) ON DELETE CASCADE,name TEXT NOT NULL,start_date TEXT NOT NULL,end_date TEXT NOT NULL,note TEXT NOT NULL DEFAULT '',PRIMARY KEY(shop_id,event_id));
        CREATE TABLE IF NOT EXISTS products(product_id TEXT NOT NULL, shop_id TEXT NOT NULL REFERENCES shops(shop_id) ON DELETE CASCADE, name TEXT NOT NULL, sku TEXT NOT NULL, category TEXT NOT NULL, cost REAL NOT NULL DEFAULT 0, price REAL NOT NULL DEFAULT 0, stock INTEGER NOT NULL DEFAULT 0 CHECK(stock>=0), unit TEXT NOT NULL DEFAULT 'unit', threshold INTEGER NOT NULL DEFAULT 0, expiry TEXT NOT NULL DEFAULT '', sold INTEGER NOT NULL DEFAULT 0, archived INTEGER NOT NULL DEFAULT 0, barcode TEXT NOT NULL DEFAULT '', lead_time_days INTEGER NOT NULL DEFAULT 7, mrp REAL NOT NULL DEFAULT 0, PRIMARY KEY(shop_id,product_id), UNIQUE(shop_id,sku));
        CREATE TABLE IF NOT EXISTS customers(customer_id TEXT NOT NULL, shop_id TEXT NOT NULL REFERENCES shops(shop_id) ON DELETE CASCADE, name TEXT NOT NULL, phone TEXT NOT NULL DEFAULT '', balance REAL NOT NULL DEFAULT 0 CHECK(balance>=0), due TEXT NOT NULL DEFAULT '', PRIMARY KEY(shop_id,customer_id));
        CREATE TABLE IF NOT EXISTS sales(sale_id TEXT NOT NULL, shop_id TEXT NOT NULL REFERENCES shops(shop_id) ON DELETE CASCADE, sale_date TEXT NOT NULL, total REAL NOT NULL, cost REAL NOT NULL, method TEXT NOT NULL, customer_name TEXT NOT NULL DEFAULT '', customer_id TEXT, item_count INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'confirmed', PRIMARY KEY(shop_id,sale_id));
        CREATE TABLE IF NOT EXISTS sale_items(line_id INTEGER PRIMARY KEY AUTOINCREMENT, shop_id TEXT NOT NULL, sale_id TEXT NOT NULL, product_id TEXT NOT NULL, product_name TEXT NOT NULL, quantity INTEGER NOT NULL CHECK(quantity>0), unit_price REAL NOT NULL, unit_cost REAL NOT NULL, returned_quantity INTEGER NOT NULL DEFAULT 0, FOREIGN KEY(shop_id,sale_id) REFERENCES sales(shop_id,sale_id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS returns(return_id TEXT NOT NULL, shop_id TEXT NOT NULL REFERENCES shops(shop_id) ON DELETE CASCADE, sale_id TEXT NOT NULL, return_date TEXT NOT NULL, amount REAL NOT NULL, reason TEXT NOT NULL DEFAULT '', PRIMARY KEY(shop_id,return_id));
        CREATE TABLE IF NOT EXISTS purchases(purchase_id TEXT NOT NULL, shop_id TEXT NOT NULL REFERENCES shops(shop_id) ON DELETE CASCADE, supplier TEXT NOT NULL, product TEXT NOT NULL, product_id TEXT, quantity INTEGER NOT NULL, cost REAL NOT NULL, purchase_date TEXT NOT NULL, invoice TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'received', batch TEXT NOT NULL DEFAULT '', expiry TEXT NOT NULL DEFAULT '', PRIMARY KEY(shop_id,purchase_id));
        CREATE TABLE IF NOT EXISTS customer_payments(payment_id TEXT NOT NULL, shop_id TEXT NOT NULL REFERENCES shops(shop_id) ON DELETE CASCADE, customer_id TEXT NOT NULL, payment_date TEXT NOT NULL, amount REAL NOT NULL, method TEXT NOT NULL, PRIMARY KEY(shop_id,payment_id), FOREIGN KEY(shop_id,customer_id) REFERENCES customers(shop_id,customer_id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS expenses(expense_id TEXT NOT NULL, shop_id TEXT NOT NULL REFERENCES shops(shop_id) ON DELETE CASCADE, category TEXT NOT NULL, amount REAL NOT NULL, expense_date TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', PRIMARY KEY(shop_id,expense_id));
        CREATE TABLE IF NOT EXISTS tasks(task_id INTEGER PRIMARY KEY AUTOINCREMENT, shop_id TEXT NOT NULL REFERENCES shops(shop_id) ON DELETE CASCADE, position INTEGER NOT NULL, text TEXT NOT NULL, detail TEXT NOT NULL, view TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0, UNIQUE(shop_id,position));
        CREATE TABLE IF NOT EXISTS idempotency(shop_id TEXT NOT NULL, request_id TEXT NOT NULL, result TEXT NOT NULL, created_at INTEGER NOT NULL, PRIMARY KEY(shop_id,request_id));
        CREATE TABLE IF NOT EXISTS inventory_movements(movement_id TEXT PRIMARY KEY, shop_id TEXT NOT NULL REFERENCES shops(shop_id) ON DELETE CASCADE, product_id TEXT NOT NULL, movement_type TEXT NOT NULL, quantity INTEGER NOT NULL, movement_date TEXT NOT NULL, reference TEXT NOT NULL DEFAULT '', batch TEXT NOT NULL DEFAULT '', expiry TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '');
        CREATE INDEX IF NOT EXISTS idx_sales_shop_date ON sales(shop_id,sale_date);
        CREATE INDEX IF NOT EXISTS idx_products_shop ON products(shop_id,archived);
        ''')
        columns={r['name'] for r in c.execute('PRAGMA table_info(products)')}
        if 'barcode' not in columns: c.execute("ALTER TABLE products ADD COLUMN barcode TEXT NOT NULL DEFAULT ''")
        if 'lead_time_days' not in columns: c.execute('ALTER TABLE products ADD COLUMN lead_time_days INTEGER NOT NULL DEFAULT 7')
        if 'mrp' not in columns: c.execute('ALTER TABLE products ADD COLUMN mrp REAL NOT NULL DEFAULT 0')
        purchase_columns={r['name'] for r in c.execute('PRAGMA table_info(purchases)')}
        for name,definition in (('status',"TEXT NOT NULL DEFAULT 'received'"),('batch',"TEXT NOT NULL DEFAULT ''"),('expiry',"TEXT NOT NULL DEFAULT ''")):
            if name not in purchase_columns: c.execute(f'ALTER TABLE purchases ADD COLUMN {name} {definition}')
        sale_columns={r['name'] for r in c.execute('PRAGMA table_info(sales)')}
        if 'status' not in sale_columns: c.execute("ALTER TABLE sales ADD COLUMN status TEXT NOT NULL DEFAULT 'confirmed'")
        item_columns={r['name'] for r in c.execute('PRAGMA table_info(sale_items)')}
        if 'returned_quantity' not in item_columns: c.execute('ALTER TABLE sale_items ADD COLUMN returned_quantity INTEGER NOT NULL DEFAULT 0')
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_products_shop_barcode ON products(shop_id,barcode) WHERE barcode<>''")
    seed_demo()


def reset_demo():
    with db() as c:
        c.execute('DELETE FROM shops WHERE shop_id=?',(DEMO_ID,))
    seed_demo()


def seed_demo():
    with db() as c:
        if c.execute('SELECT 1 FROM shops WHERE shop_id=?', (DEMO_ID,)).fetchone(): return
        today = date.today()
        c.execute('BEGIN IMMEDIATE')
        c.execute('INSERT INTO shops(shop_id,owner_user_id,name,owner_name,category,address,currency) VALUES(?,?,?,?,?,?,?)', (DEMO_ID,None,'Sharma General Store','Ravi Kumar','Grocery','12 Market Road, Pune','INR'))
        products = [
            ('p1','Fresh milk 500ml','FM-005','Dairy',25,30,18,'bottle',8,(today+timedelta(days=2)).isoformat(),42,'8901234500017'),
            ('p2','Tomatoes 1kg','VG-021','Produce',28,40,9,'kg',6,(today+timedelta(days=3)).isoformat(),28,'8901234500024'),
            ('p3','Farm eggs 6 pack','EG-006','Dairy',42,55,13,'pack',5,(today+timedelta(days=4)).isoformat(),19,'8901234500031'),
            ('p4','Wheat flour 5kg','AT-005','Pantry',205,245,32,'bag',8,'',24,'8901234500048'),
            ('p5','Cooking oil 1L','OI-001','Pantry',122,149,11,'bottle',6,'',17,'8901234500055'),
            ('p6','Tea biscuits','SN-014','Snacks',18,25,56,'pack',12,'',62,'8901234500062'),
            ('p7','Basmati rice 1kg','RC-001','Pantry',78,99,85,'kg',10,'',31,'8901234500079'),
            ('p8','Bananas 1 dozen','VG-012','Produce',32,50,4,'dozen',6,(today+timedelta(days=1)).isoformat(),23,'8901234500086'),
        ]
        c.executemany('INSERT INTO products(product_id,shop_id,name,sku,category,cost,price,stock,unit,threshold,expiry,sold,archived,barcode,lead_time_days) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', [(pid,DEMO_ID,name,sku,cat,cost,price,stock,unit,threshold,expiry,sold,0,barcode,[2,2,3,5,4,2,5,2][i]) for i,(pid,name,sku,cat,cost,price,stock,unit,threshold,expiry,sold,barcode) in enumerate(products)])
        customers=[('c1','Anil Kumar','9876543210',2500,(today-timedelta(days=4)).isoformat()),('c2','Meena Stores','9822012345',1850,(today+timedelta(days=6)).isoformat()),('c3','Suresh Patil','9765432109',2400,(today-timedelta(days=1)).isoformat())]
        c.executemany('INSERT INTO customers VALUES(?,?,?,?,?,?)', [(cid,DEMO_ID,n,p,b,d) for cid,n,p,b,d in customers])
        # Ninety days of realistic, item-level shop history gives trend, product,
        # and category analysis a consistent source rather than unrelated totals.
        demand=[0,1,2,3,4,5,6,7,0,0,5,3,1,4,5,0,6,2]
        product_by_id={p[0]:p for p in products}
        rolling={p[0]:0 for p in products}
        for days_ago in range(89,-1,-1):
            day=today-timedelta(days=days_ago)
            for slot in range(3):
                picked={}
                count=2+(day.day+slot)%3
                offset=days_ago*7+slot*5
                for ix in range(count):
                    product_index=demand[(offset+ix*3)%len(demand)]
                    pid=products[product_index][0]
                    if days_ago<14 and pid=='p8':pid='p4'
                    picked[pid]=picked.get(pid,0)+1+(days_ago+slot+ix)%2
                lines=[];revenue=0;cost_total=0
                for pid,qty in picked.items():
                    p=product_by_id[pid];price=p[5];cost=p[4]
                    revenue+=price*qty;cost_total+=cost*qty
                    lines.append((pid,p[1],qty,price,cost))
                    if days_ago<30: rolling[pid]+=qty
                method=['UPI','Cash','UPI','Card'][slot]
                sid=f'D-{day.strftime("%y%m%d")}-{slot+1}'
                c.execute("INSERT INTO sales(sale_id,shop_id,sale_date,total,cost,method,customer_name,customer_id,item_count,status) VALUES(?,?,?,?,?,?,?,?,?,'confirmed')",(sid,DEMO_ID,day.isoformat(),revenue,cost_total,method,'',None,sum(x[2] for x in lines)))
                c.executemany('INSERT INTO sale_items(shop_id,sale_id,product_id,product_name,quantity,unit_price,unit_cost) VALUES(?,?,?,?,?,?,?)',[(DEMO_ID,sid,*line) for line in lines])
                c.executemany('INSERT INTO inventory_movements VALUES(?,?,?,?,?,?,?,?,?,?)',[(f'{sid}-{line[0]}',DEMO_ID,line[0],'sale',-line[2],day.isoformat(),sid,'','','Historical demo sale') for line in lines])
        for pid,units in rolling.items():
            c.execute('UPDATE products SET sold=? WHERE shop_id=? AND product_id=?',(units,DEMO_ID,pid))
        c.execute('INSERT INTO purchases(purchase_id,shop_id,supplier,product,product_id,quantity,cost,purchase_date,invoice,status,batch,expiry) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',('P-2408',DEMO_ID,'Sharma Dairy','Fresh milk 500ml','p1',24,25,today.isoformat(),'SD-581','received','MILK-2408',(today+timedelta(days=2)).isoformat()))
        c.execute('INSERT INTO inventory_movements VALUES(?,?,?,?,?,?,?,?,?,?)',('demo-receipt-milk',DEMO_ID,'p1','stock-in',24,today.isoformat(),'P-2408','MILK-2408',(today+timedelta(days=2)).isoformat(),'Demo supplier receipt'))
        c.executemany('INSERT INTO expenses VALUES(?,?,?,?,?,?)', [('e1',DEMO_ID,'Rent',12000,today.isoformat(),'Shop rent'),('e2',DEMO_ID,'Electricity',1800,today.isoformat(),'Monthly bill')])
        tasks=[('Check bananas stock','4 dozen on hand · below the 6-dozen reorder threshold','inventory',0),('Check bananas expiry','4 dozen · expires tomorrow','inventory',0),('Collect payment from Anil','Overdue by 4 days · ₹2,500','payments',0),('Review yesterday sales','Daily close','sales',1)]
        c.executemany('INSERT INTO tasks(shop_id,position,text,detail,view,done) VALUES(?,?,?,?,?,?)',[(DEMO_ID,i,*t) for i,t in enumerate(tasks)])
        c.commit()


def session_user(handler):
    raw = handler.cookies.get(SESSION_COOKIE)
    if not raw: return None
    token_hash=hashlib.sha256(raw.encode()).hexdigest()
    with db() as c:
        row=c.execute('SELECT u.user_id,u.identifier,s.shop_id,s.name,s.owner_name,s.category,s.address FROM sessions x JOIN users u ON x.user_id=u.user_id JOIN shops s ON s.owner_user_id=u.user_id WHERE x.token_hash=? AND x.expires_at>?',(token_hash,int(time.time()))).fetchone()
        return dict(row) if row else None


def load_workspace(shop_id):
    with db() as c:
        sh=c.execute('SELECT shop_id,name,owner_name,category,address,currency,revision FROM shops WHERE shop_id=?',(shop_id,)).fetchone()
        if not sh: raise ValueError('Shop not found')
        data={'shop':{'name':sh['name'],'owner':sh['owner_name'],'category':sh['category'],'address':sh['address'],'currency':sh['currency']},'revision':sh['revision']}
        data['products']=[{**dict(r),'id':r['product_id'],'archived':bool(r['archived'])} for r in c.execute('SELECT * FROM products WHERE shop_id=? ORDER BY rowid',(shop_id,))]
        for p in data['products']: p.pop('product_id');p.pop('shop_id');p['expiry']=p.pop('expiry') or '';p['leadTimeDays']=p.pop('lead_time_days')
        data['customers']=[{**dict(r),'id':r['customer_id'],'history':[]} for r in c.execute('SELECT * FROM customers WHERE shop_id=? ORDER BY rowid',(shop_id,))]
        for cu in data['customers']: cu.pop('customer_id');cu.pop('shop_id')
        pay={r['customer_id']:[] for r in c.execute('SELECT customer_id FROM customers WHERE shop_id=?',(shop_id,))}
        for r in c.execute('SELECT * FROM customer_payments WHERE shop_id=? ORDER BY rowid',(shop_id,)):
            pay.setdefault(r['customer_id'],[]).append({'id':r['payment_id'],'date':r['payment_date'],'amount':r['amount'],'method':r['method']})
        for cu in data['customers']:cu['history']=pay.get(cu['id'],[])
        data['sales']=[{'id':r['sale_id'],'date':r['sale_date'],'total':r['total'],'cost':r['cost'],'method':r['method'],'customer':r['customer_name'],'items':r['item_count'],'status':r['status'],'details':[{'productId':x['product_id'],'product':x['product_name'],'qty':x['quantity'],'returned':x['returned_quantity'],'price':x['unit_price'],'cost':x['unit_cost']} for x in c.execute('SELECT * FROM sale_items WHERE shop_id=? AND sale_id=?',(shop_id,r['sale_id']))]} for r in c.execute('SELECT * FROM sales WHERE shop_id=? ORDER BY sale_date,sale_id',(shop_id,))]
        data['returns']=[{'id':r['return_id'],'saleId':r['sale_id'],'date':r['return_date'],'amount':r['amount'],'reason':r['reason']} for r in c.execute('SELECT * FROM returns WHERE shop_id=? ORDER BY return_date,return_id',(shop_id,))]
        data['purchases']=[{'id':r['purchase_id'],'supplier':r['supplier'],'product':r['product'],'productId':r['product_id'],'qty':r['quantity'],'cost':r['cost'],'date':r['purchase_date'],'invoice':r['invoice'],'status':r['status'],'batch':r['batch'],'expiry':r['expiry'],'confirmed':r['status']=='received'} for r in c.execute('SELECT * FROM purchases WHERE shop_id=? ORDER BY purchase_date,purchase_id',(shop_id,))]
        data['expenses']=[{'id':r['expense_id'],'category':r['category'],'amount':r['amount'],'date':r['expense_date'],'note':r['note']} for r in c.execute('SELECT * FROM expenses WHERE shop_id=? ORDER BY expense_date,expense_id',(shop_id,))]
        data['tasks']=[{'text':r['text'],'detail':r['detail'],'view':r['view'],'done':bool(r['done'])} for r in c.execute('SELECT * FROM tasks WHERE shop_id=? ORDER BY position',(shop_id,))]
        return data


def replace_workspace(shop_id, data):
    shop=data.get('shop') or {}; revision=int(data.get('revision') or 0)
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute('SELECT revision FROM shops WHERE shop_id=?',(shop_id,)).fetchone()
        if not row: raise ValueError('Shop not found')
        if revision and row['revision'] != revision:
            c.rollback(); raise RuntimeError('This workspace changed in another tab. Refresh and retry.')
        previous_stock={r['product_id']:r['stock'] for r in c.execute('SELECT product_id,stock FROM products WHERE shop_id=?',(shop_id,))}
        c.execute('UPDATE shops SET name=?,owner_name=?,category=?,address=?,currency=?,revision=revision+1 WHERE shop_id=?',(shop.get('name','My Shop'),shop.get('owner','Shop owner'),shop.get('category','Grocery'),shop.get('address',''),shop.get('currency','INR'),shop_id))
        for table in ('sale_items','sales','returns','purchases','customer_payments','customers','products','expenses','tasks'):
            c.execute(f'DELETE FROM {table} WHERE shop_id=?',(shop_id,))
        for p in data.get('products',[]):
            pid=str(p.get('id') or uuid.uuid4());stock=max(0,int(p.get('stock') or 0))
            c.execute('INSERT INTO products(product_id,shop_id,name,sku,category,cost,price,stock,unit,threshold,expiry,sold,archived,barcode,lead_time_days,mrp) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(pid,shop_id,p.get('name',''),p.get('sku',''),p.get('category','Other'),float(p.get('cost') or 0),float(p.get('price') or 0),stock,p.get('unit','unit'),max(0,int(p.get('threshold') or 0)),p.get('expiry') or '',max(0,int(p.get('sold') or 0)),int(bool(p.get('archived'))),str(p.get('barcode') or ''),max(1,int(p.get('leadTimeDays') or 7)),max(0,float(p.get('mrp') or 0))))
            delta=stock-previous_stock.get(pid,0)
            if delta:c.execute('INSERT INTO inventory_movements VALUES(?,?,?,?,?,?,?,?,?,?)',(uuid.uuid4().hex,shop_id,pid,'opening' if pid not in previous_stock else 'adjustment',delta,date.today().isoformat(),'','',p.get('expiry') or '', 'Opening stock or manual stock correction'))
        for cu in data.get('customers',[]):
            c.execute('INSERT INTO customers VALUES(?,?,?,?,?,?)',(str(cu.get('id') or uuid.uuid4()),shop_id,cu.get('name',''),cu.get('phone',''),max(0,float(cu.get('balance') or 0)),cu.get('due') or ''))
            for i,h in enumerate(cu.get('history',[])):
                c.execute('INSERT INTO customer_payments VALUES(?,?,?,?,?,?)',(str(h.get('id') or f'{cu.get("id")}-pay-{i}'),shop_id,str(cu.get('id')),h.get('date',date.today().isoformat()),float(h.get('amount') or 0),h.get('method','Cash')))
        for s in data.get('sales',[]):
            sid=str(s.get('id') or uuid.uuid4())
            c.execute("INSERT INTO sales(sale_id,shop_id,sale_date,total,cost,method,customer_name,customer_id,item_count,status) VALUES(?,?,?,?,?,?,?,?,?,?)",(sid,shop_id,s.get('date',date.today().isoformat()),float(s.get('total') or 0),float(s.get('cost') or 0),s.get('method','Cash'),s.get('customer') or '',None,int(s.get('items') or 0),s.get('status') or 'confirmed'))
            for line in s.get('details',[]):
                c.execute('INSERT INTO sale_items(shop_id,sale_id,product_id,product_name,quantity,unit_price,unit_cost,returned_quantity) VALUES(?,?,?,?,?,?,?,?)',(shop_id,sid,str(line.get('productId') or ''),str(line.get('product') or ''),int(line.get('qty') or 0),float(line.get('price') or 0),float(line.get('cost') or 0),int(line.get('returned') or 0)))
        for r in data.get('returns',[]):
            c.execute('INSERT INTO returns VALUES(?,?,?,?,?,?)',(str(r.get('id') or uuid.uuid4()),shop_id,str(r.get('saleId') or ''),str(r.get('date') or date.today().isoformat()),float(r.get('amount') or 0),str(r.get('reason') or '')))
        for p in data.get('purchases',[]):
            c.execute('INSERT INTO purchases(purchase_id,shop_id,supplier,product,product_id,quantity,cost,purchase_date,invoice,status,batch,expiry) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(str(p.get('id') or uuid.uuid4()),shop_id,p.get('supplier','Supplier'),p.get('product',''),p.get('productId'),int(p.get('qty') or 0),float(p.get('cost') or 0),p.get('date',date.today().isoformat()),p.get('invoice',''),p.get('status') or 'received',p.get('batch') or '',p.get('expiry') or ''))
        for e in data.get('expenses',[]):
            c.execute('INSERT INTO expenses VALUES(?,?,?,?,?,?)',(str(e.get('id') or uuid.uuid4()),shop_id,e.get('category','Miscellaneous'),float(e.get('amount') or 0),e.get('date',date.today().isoformat()),e.get('note','')))
        for i,t in enumerate(data.get('tasks',[])):
            c.execute('INSERT INTO tasks(shop_id,position,text,detail,view,done) VALUES(?,?,?,?,?,?)',(shop_id,i,t.get('text',''),t.get('detail',''),t.get('view','overview'),int(bool(t.get('done')))))
        c.commit()
    return load_workspace(shop_id)


def inventory_insights(shop_id):
    """Explainable moving-average forecasts built only from confirmed sale lines."""
    today=date.today(); start30=(today-timedelta(days=29)).isoformat(); start90=(today-timedelta(days=89)).isoformat(); start365=(today-timedelta(days=364)).isoformat()
    with db() as c:
        products=[dict(r) for r in c.execute('SELECT * FROM products WHERE shop_id=? AND archived=0',(shop_id,))]
        lines=c.execute('''SELECT si.product_id,s.sale_date,SUM(si.quantity-si.returned_quantity) quantity
            FROM sale_items si JOIN sales s ON s.shop_id=si.shop_id AND s.sale_id=si.sale_id
            WHERE si.shop_id=? AND s.sale_date>=? AND s.status='confirmed' GROUP BY si.product_id,s.sale_date''',(shop_id,start365)).fetchall()
        orders={r['product_id']:r['quantity'] for r in c.execute("SELECT product_id,SUM(quantity) quantity FROM purchases WHERE shop_id=? AND status IN ('draft','ordered') GROUP BY product_id",(shop_id,))}
    per_product={p['product_id']:{} for p in products}
    for line in lines:
        bucket=per_product.get(line['product_id'])
        if bucket is not None: bucket[line['sale_date']]=int(line['quantity'])
    insights=[]
    for p in products:
        daily=per_product[p['product_id']];units=lambda days:sum(q for d,q in daily.items() if d >= (today-timedelta(days=days-1)).isoformat())
        u_today=daily.get(today.isoformat(),0);u7=units(7);prev7=sum(q for d,q in daily.items() if (today-timedelta(days=13)).isoformat()<=d<(today-timedelta(days=6)).isoformat())
        u14=units(14);u30=units(30);prev30=sum(q for d,q in daily.items() if (today-timedelta(days=59)).isoformat()<=d<(today-timedelta(days=29)).isoformat())
        u90=units(90);sale_days30=sum(1 for d,q in daily.items() if d>=start30 and q>0)
        history_days=min(365,(today-date.fromisoformat(min(daily))).days+1) if daily else 0
        enough=sale_days30>=5 and history_days>=7 and u30>0
        avg7=u7/7;avg30=u30/30
        avg_daily=round(.65*avg7+.35*avg30,3) if enough else 0
        trend='stable'
        if u7>=5 and prev7>=5 and u7>prev7*1.2:trend='increasing'
        elif prev7>=5 and u7<prev7*.8:trend='decreasing'
        elif u30>=10 and prev30>=10 and u30>prev30*1.15:trend='increasing'
        elif prev30>=10 and u30<prev30*.85:trend='decreasing'
        stock=int(p['stock']);lead=max(1,int(p['lead_time_days'] or 7));safety=max(int(p['threshold'] or 0),int((avg_daily*2)+.999)) if enough else int(p['threshold'] or 0)
        on_order=int(orders.get(p['product_id'],0));days_to_runout=(round(stock/avg_daily,1) if enough and avg_daily else None)
        reorder_point=(avg_daily*lead+safety) if enough else 0
        days_to_reorder=(max(0,round((stock-on_order-reorder_point)/avg_daily,1)) if enough and avg_daily else None)
        stockout=((today+timedelta(days=max(0,int(stock/avg_daily+.999)))).isoformat() if days_to_runout is not None else None)
        suggested=max(0,int(avg_daily*lead+safety-stock-on_order+.999)) if enough else 0
        days_cover=stock/avg_daily if enough and avg_daily else None
        fast=enough and u7>=7 and trend=='increasing'
        slow=enough and history_days>=14 and u14<=4
        excess=enough and days_cover is not None and days_cover>=60
        insights.append({'productId':p['product_id'],'name':p['name'],'sku':p['sku'],'barcode':p['barcode'],'category':p['category'],'unit':p['unit'],'stock':stock,'threshold':int(p['threshold'] or 0),'leadTimeDays':lead,'purchasePrice':p['cost'],'sellingPrice':p['price'],'salesToday':u_today,'sales7':u7,'previous7':prev7,'sales14':u14,'sales30':u30,'previous30':prev30,'sales90':u90,'saleDays30':sale_days30,'historyDays':history_days,'averageDaily':avg_daily,'averageWeekly':round(avg_daily*7,1) if enough else None,'trend':trend,'enoughHistory':enough,'daysToStockout':days_to_runout,'stockoutDate':stockout,'daysToReorder':days_to_reorder,'safetyStock':safety,'onOrder':on_order,'suggestedQty':suggested,'fastMoving':fast,'slowMoving':slow,'excessStock':excess,'lowStock':stock<=int(p['threshold'] or 0),'seasonalityAvailable':history_days>=730})
    return {'asOf':today.isoformat(),'method':'Weighted moving average: 65% recent seven-day demand and 35% thirty-day demand. Reorder covers supplier lead time plus safety stock, less on-hand and pending order units.','historyDaysAvailable':max((x['historyDays'] for x in insights),default=0),'items':insights}


def pricing_intelligence(shop_id):
    today=date.today(); fresh=(today-timedelta(days=30)).isoformat()
    insight_by={x['productId']:x for x in inventory_insights(shop_id)['items']}
    with db() as c:
        products=[dict(r) for r in c.execute("SELECT * FROM products WHERE shop_id=? AND archived=0",(shop_id,))]
        prices=[dict(r) for r in c.execute("SELECT * FROM competitor_prices WHERE shop_id=? ORDER BY checked_on DESC",(shop_id,))]
        events=[dict(r) for r in c.execute("SELECT * FROM seasonal_events WHERE shop_id=? ORDER BY start_date",(shop_id,))]
        changes=[dict(r) for r in c.execute("SELECT * FROM price_history WHERE shop_id=? ORDER BY approved_at DESC LIMIT 100",(shop_id,))]
        oldest=c.execute("SELECT MIN(s.sale_date) d FROM sales s JOIN sale_items i ON i.shop_id=s.shop_id AND i.sale_id=s.sale_id WHERE s.shop_id=? AND s.status='confirmed'",(shop_id,)).fetchone()['d']
        monthly=c.execute("SELECT i.product_id,substr(s.sale_date,1,4) year,substr(s.sale_date,6,2) month,SUM(i.quantity-i.returned_quantity) units FROM sale_items i JOIN sales s ON s.shop_id=i.shop_id AND s.sale_id=i.sale_id WHERE s.shop_id=? AND s.status='confirmed' GROUP BY i.product_id,substr(s.sale_date,1,4),substr(s.sale_date,6,2)",(shop_id,)).fetchall()
    history_days=(date.today()-date.fromisoformat(oldest)).days+1 if oldest else 0
    months={}; yearly={}
    for row in monthly:
        pid=row['product_id'];mi=int(row['month'])-1;amount=int(row['units'] or 0)
        months.setdefault(pid,[0]*12)[mi]+=amount
        yearly.setdefault(pid,{}).setdefault(int(row['year']),[0]*12)[mi]+=amount
    recommendations=[]
    for p in products:
        pid=p['product_id'];ins=insight_by.get(pid,{}); recorded=[x for x in prices if x['product_id']==pid and x['checked_on']>=fresh]
        vals=sorted(x['price'] for x in recorded); median=(vals[len(vals)//2] if len(vals)%2 else (vals[len(vals)//2-1]+vals[len(vals)//2])/2) if vals else None
        avg=ins.get('averageDaily') or 0; cover=ins.get('daysToStockout'); trend=ins.get('trend','stable'); margin=((p['price']-p['cost'])/p['price']*100) if p['price'] else 0
        action='REVIEW COMPETITION' if ins.get('enoughHistory') else 'INSUFFICIENT DATA'; suggested=None; reason=('Add a recent verified neighbourhood price to compare.' if ins.get('enoughHistory') else 'Build sales history and add verified competitor prices.')
        seasonal='insufficient';seasonal_change=None
        if history_days>=730:
            current_month=today.month-1; patterns=[]
            for year_values in yearly.get(pid,{}).values():
                baseline=sum(year_values)/12
                if sum(1 for units in year_values if units>0)>=9 and baseline>0: patterns.append(year_values[current_month]/baseline)
            if len(patterns)>=2: seasonal_change=round((sum(patterns)/len(patterns)-1)*100,1)
            if len(patterns)>=2 and sum(1 for ratio in patterns if ratio>1.35)>=2: seasonal='rising'
            elif len(patterns)>=2 and sum(1 for ratio in patterns if ratio<.65)>=2: seasonal='falling'
            elif len(patterns)>=2: seasonal='stable'
        if median is not None and ins.get('enoughHistory') and p['cost']>0:
            if ((cover is not None and cover < ins.get('leadTimeDays',7)) or seasonal=='rising') and p['price']<median and (trend=='increasing' or seasonal=='rising'):
                candidate=min(median,(p['price']+median)/2); action='INCREASE PRICE'; reason='Demand is rising, stock cover is tight, and recent local prices are higher.'
            elif (ins.get('excessStock') or seasonal=='falling') and (trend=='decreasing' or seasonal=='falling') and p['price']>median and median>=p['cost']:
                candidate=median; action='DECREASE PRICE'; reason='Stock cover is high and demand is falling; local median remains above cost.'
            else: candidate=p['price'];action='MAINTAIN PRICE';reason='Current price is consistent with cost, demand, stock and recent local records.'
            floor=p['cost']/.95 if p['cost'] else 0
            if p['price']<floor:
                action='INCREASE PRICE';reason='Current price is below the configured 5% gross-margin floor; review a safe increase.';candidate=max(floor,median)
            else:candidate=max(candidate,floor)
            if p.get('mrp') and p['mrp']>0:candidate=min(candidate,p['mrp'])
            if candidate<p['cost'] or (p.get('mrp') and candidate>p['mrp']):candidate=None;action='REVIEW';reason='Price bounds conflict; review cost and MRP before changing price.'
            suggested=round(candidate,2) if candidate is not None and action!='MAINTAIN PRICE' else None
        recommendations.append({'productId':pid,'name':p['name'],'sku':p['sku'],'cost':p['cost'],'price':p['price'],'mrp':p.get('mrp') or None,'marginPercent':round(margin,1),'stock':p['stock'],'unit':p['unit'],'demand':trend if ins.get('enoughHistory') else 'insufficient','daysToStockout':cover,'competitorCount':len(recorded),'competitorMedian':round(median,2) if median is not None else None,'competitorLow':min(vals) if vals else None,'competitorHigh':max(vals) if vals else None,'lastChecked':max((x['checked_on'] for x in recorded),default=None),'action':action,'suggestedPrice':suggested,'reason':reason,'seasonalDemand':seasonal,'seasonalChangePercent':seasonal_change,'monthlyUnits':months.get(pid,[0]*12)})
    return {'asOf':today.isoformat(),'historyDays':history_days,'seasonalityAvailable':history_days>=730,'monthlyLabels':['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'],'products':recommendations,'competitorPrices':prices,'events':events,'priceHistory':changes}

class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs): super().__init__(*args,directory=str(ROOT),**kwargs)
    def log_message(self, fmt, *args): print('%s - %s' % (self.address_string(),fmt%args))
    def end_headers(self):
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'strict-origin-when-cross-origin')
        self.send_header('Permissions-Policy', 'camera=(self), microphone=(), geolocation=()')
        if SESSION_COOKIE_SECURE:
            self.send_header('Strict-Transport-Security', 'max-age=31536000; includeSubDomains')
        super().end_headers()
    @property
    def cookies(self):
        jar=cookies.SimpleCookie(self.headers.get('Cookie',''))
        return {k:v.value for k,v in jar.items()}
    def json_body(self, max_size=2_000_000):
        size=int(self.headers.get('Content-Length','0'))
        if size>max_size: raise ValueError('Request too large')
        return json.loads(self.rfile.read(size) or b'{}')
    def send_json(self,obj,status=200,headers=None):
        body=json.dumps(obj,ensure_ascii=False,separators=(',',':')).encode()
        self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store')
        for k,v in (headers or {}).items(): self.send_header(k,v)
        self.end_headers();self.wfile.write(body)
    def send_error_json(self,msg,status=400): self.send_json({'error':msg},status)
    def session_cookie(self, token, max_age=SESSION_SECONDS):
        secure = '; Secure' if SESSION_COOKIE_SECURE else ''
        return f'{SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={max_age}{secure}'
    def api_shop(self):
        q=parse_qs(urlparse(self.path).query)
        if q.get('demo',[''])[0]=='1': return DEMO_ID
        user=session_user(self)
        return user['shop_id'] if user else None
    def route_page(self,path):
        if path in ('/','/index.html'): return ROOT/'index.html'
        if path.startswith('/app/') and Path(path).suffix in ('.js','.css','.png','.svg','.woff','.woff2'):
            return None
        if path.rstrip('/') in ('/login','/register') and session_user(self):
            self.send_response(302);self.send_header('Location','/app');self.end_headers();return False
        if path.rstrip('/')=='/login': return ROOT/'login/index.html'
        if path.rstrip('/')=='/register': return ROOT/'register/index.html'
        if path.rstrip('/')=='/app' or path.startswith('/app/'):
            if parse_qs(urlparse(self.path).query).get('demo',[''])[0] != '1' and not session_user(self):
                self.send_response(302);self.send_header('Location','/login?next='+('/'+path.lstrip('/')));self.end_headers();return False
            return ROOT/'app/index.html'
        return None
    def do_GET(self):
        parsed=urlparse(self.path);path=unquote(parsed.path)
        if path == '/healthz':
            try:
                with db() as c: c.execute('SELECT 1').fetchone()
                return self.send_json({'status':'ok'})
            except sqlite3.Error:
                return self.send_json({'status':'error'},503)
        if path.startswith('/api/'):
            if path=='/api/session':
                u=session_user(self);return self.send_json({'authenticated':bool(u),'user':u})
            if path=='/api/profile':
                user=session_user(self)
                if not user:return self.send_error_json('Sign in required',401)
                with db() as c:
                    row=c.execute('SELECT u.identifier,u.created_at,s.name shop_name,s.owner_name,s.category,s.address,p.mime,p.image FROM users u JOIN shops s ON s.owner_user_id=u.user_id LEFT JOIN profile_photos p ON p.user_id=u.user_id WHERE u.user_id=?',(user['user_id'],)).fetchone()
                import base64
                return self.send_json({'profile':{'name':row['owner_name'],'identifier':row['identifier'],'createdAt':row['created_at'],'shop':{'name':row['shop_name'],'category':row['category'],'address':row['address']},'photo':('data:'+row['mime']+';base64,'+base64.b64encode(row['image']).decode()) if row['image'] else ''}})
            if path=='/api/workspace':
                shop=self.api_shop()
                if not shop:return self.send_error_json('Sign in required',401)
                try:return self.send_json({'workspace':load_workspace(shop),'demo':shop==DEMO_ID})
                except Exception as e:return self.send_error_json(str(e),404)
            if path=='/api/inventory-insights':
                shop=self.api_shop()
                if not shop:return self.send_error_json('Sign in required',401)
                return self.send_json(inventory_insights(shop))
            if path=='/api/intelligence':
                shop=self.api_shop()
                if not shop:return self.send_error_json('Sign in required',401)
                return self.send_json(pricing_intelligence(shop))
            return self.send_error_json('Not found',404)
        route=self.route_page(path)
        if route is False:return
        if route is not None:
            if not route.exists():return self.send_error(404)
            return self.serve_file(route)
        if path in ('/favicon.ico',): return self.send_error(404)
        target=(ROOT/path.lstrip('/')).resolve()
        public_files={ROOT/'auth.js',ROOT/'auth.css',ROOT/'auth-pages.js',ROOT/'store.js',ROOT/'app/app.js'}
        if target not in public_files and not (ROOT/'assets') in target.parents:
            return self.send_error(404)
        if ROOT not in target.parents or not target.is_file(): return self.send_error(404)
        self.serve_file(target)
    def serve_file(self,path):
        body=path.read_bytes();typ=mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
        if path.suffix=='.js':typ='text/javascript'
        self.send_response(200);self.send_header('Content-Type',typ+'; charset=utf-8' if typ.startswith('text/') or 'javascript' in typ else typ);self.send_header('Content-Length',str(len(body)));self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(body)
    def do_POST(self):
        path=urlparse(self.path).path
        try: body=self.json_body()
        except Exception as e:return self.send_error_json(str(e),400)
        if path=='/api/register':
            identifier=str(body.get('identifier','')).strip().lower();password=str(body.get('password',''));owner=str(body.get('owner','')).strip();shop_name=str(body.get('shopName','')).strip()
            if not identifier or not owner or not shop_name or len(password)<8:return self.send_error_json('Complete all required fields and use a password of at least 8 characters.')
            salt=secrets.token_bytes(16);ph=hashlib.pbkdf2_hmac('sha256',password.encode(),salt,310000)
            uid='u-'+uuid.uuid4().hex;sid='shop-'+uuid.uuid4().hex
            try:
                with db() as c:
                    c.execute('BEGIN IMMEDIATE');c.execute('INSERT INTO users VALUES(?,?,?,?,?)',(uid,identifier,salt,ph,int(time.time())));c.execute('INSERT INTO shops(shop_id,owner_user_id,name,owner_name,category,address,currency) VALUES(?,?,?,?,?,?,?)',(sid,uid,shop_name,owner,str(body.get('category') or 'Grocery'),str(body.get('address') or ''),'INR'));c.commit()
                token=secrets.token_urlsafe(32);with_session=hashlib.sha256(token.encode()).hexdigest()
                with db() as c:c.execute('INSERT INTO sessions VALUES(?,?,?)',(with_session,uid,int(time.time())+SESSION_SECONDS))
                return self.send_json({'ok':True},201,{ 'Set-Cookie':self.session_cookie(token) })
            except sqlite3.IntegrityError:return self.send_error_json('An account with this email or phone already exists.',409)
        if path=='/api/login':
            identifier=str(body.get('identifier','')).strip().lower();password=str(body.get('password',''))
            with db() as c:u=c.execute('SELECT * FROM users WHERE identifier=?',(identifier,)).fetchone()
            if not u or not secrets.compare_digest(hashlib.pbkdf2_hmac('sha256',password.encode(),u['salt'],310000),u['password_hash']):return self.send_error_json('The account or password is incorrect.',401)
            token=secrets.token_urlsafe(32)
            with db() as c: c.execute('INSERT INTO sessions VALUES(?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),u['user_id'],int(time.time())+SESSION_SECONDS))
            return self.send_json({'ok':True},200,{ 'Set-Cookie':self.session_cookie(token) })
        shop=self.api_shop()
        if not shop:return self.send_error_json('Sign in required',401)
        if path=='/api/competitor-prices':
            item=body;pid=str(item.get('productId') or '');name=str(item.get('competitor') or '').strip();locality=str(item.get('locality') or '').strip();price=float(item.get('price') or 0);checked=str(item.get('checkedOn') or date.today().isoformat())
            if not pid or not name or not math.isfinite(price) or price<=0:return self.send_error_json('Choose a product, competitor and valid price.')
            try: date.fromisoformat(checked)
            except ValueError:return self.send_error_json('Choose a valid price-check date.')
            with db() as c:
                if not c.execute('SELECT 1 FROM products WHERE shop_id=? AND product_id=? AND archived=0',(shop,pid)).fetchone():return self.send_error_json('Product not found.',404)
                c.execute('INSERT INTO competitor_prices VALUES(?,?,?,?,?,?,?,?)',(uuid.uuid4().hex,shop,pid,name,locality,price,checked,str(item.get('sourceNote') or '')[:240]))
            return self.send_json(pricing_intelligence(shop),201)
        if path=='/api/seasonal-events':
            item=body;name=str(item.get('name') or '').strip();start=str(item.get('startDate') or '');end=str(item.get('endDate') or '')
            try: start_date=date.fromisoformat(start);end_date=date.fromisoformat(end)
            except ValueError:return self.send_error_json('Enter an event and valid dates.')
            if not name or end_date<start_date:return self.send_error_json('Enter an event and a valid date range.')
            eid=uuid.uuid4().hex
            with db() as c:c.execute('INSERT INTO seasonal_events VALUES(?,?,?,?,?,?)',(eid,shop,name,start,end,str(item.get('note') or '')[:240]))
            return self.send_json(pricing_intelligence(shop),201)
        if path=='/api/pricing/apply':
            pid=str(body.get('productId') or '');price=float(body.get('price') or 0)
            if not math.isfinite(price) or price<=0:return self.send_error_json('Enter a valid positive selling price.')
            with db() as c:
                product=c.execute('SELECT * FROM products WHERE shop_id=? AND product_id=? AND archived=0',(shop,pid)).fetchone()
                if not product:return self.send_error_json('Product not found.',404)
                if price<product['cost']/.95:return self.send_error_json('Price is below the minimum 5% gross-margin floor.',409)
                if product['mrp'] and price>product['mrp']:return self.send_error_json('Price cannot exceed the configured MRP.',409)
                c.execute('BEGIN IMMEDIATE');c.execute('UPDATE products SET price=? WHERE shop_id=? AND product_id=?',(price,shop,pid));user=session_user(self);approver='Demo user' if shop==DEMO_ID else (user['identifier'] if user else 'Shop owner');c.execute('INSERT INTO price_history VALUES(?,?,?,?,?,?,?)',(uuid.uuid4().hex,shop,pid,product['price'],price,approver,date.today().isoformat()));c.commit()
            return self.send_json(pricing_intelligence(shop))
        if path=='/api/demo/reset' and shop==DEMO_ID:
            reset_demo();return self.send_json({'workspace':load_workspace(DEMO_ID)})
        if path=='/api/sales':return self.record_sale(shop,body)
        if path=='/api/returns':return self.record_return(shop,body)
        if path=='/api/purchase-drafts':return self.create_purchase_draft(shop,body)
        if path in ('/api/stock-in','/api/purchases'):return self.receive_stock(shop,body)
        return self.send_error_json('Not found',404)
    def do_PUT(self):
        if urlparse(self.path).path=='/api/profile':
            user=session_user(self)
            if not user:return self.send_error_json('Sign in required',401)
            try:
                body=self.json_body(3_000_000);name=str(body.get('name','')).strip()
                if not 1<=len(name)<=80:return self.send_error_json('Name must be 1–80 characters.')
                image=body.get('image');mime=str(body.get('mime') or '')
                if image is not None:
                    import base64,binascii
                    if mime not in ('image/jpeg','image/png','image/webp'):return self.send_error_json('Use a JPG, PNG or WEBP image.')
                    try:raw=base64.b64decode(image,validate=True)
                    except (binascii.Error,ValueError):return self.send_error_json('Invalid image data.')
                    if not raw or len(raw)>2_000_000:return self.send_error_json('Image must be 2 MB or smaller.')
                    valid=(mime=='image/jpeg' and raw.startswith(b'\xff\xd8\xff')) or (mime=='image/png' and raw.startswith(b'\x89PNG\r\n\x1a\n')) or (mime=='image/webp' and len(raw)>12 and raw[:4]==b'RIFF' and raw[8:12]==b'WEBP')
                    if not valid:return self.send_error_json('File contents do not match the selected image type.')
                with db() as c:
                    c.execute('BEGIN IMMEDIATE');c.execute('UPDATE shops SET owner_name=?,revision=revision+1 WHERE owner_user_id=?',(name,user['user_id']))
                    if image is not None:c.execute('INSERT INTO profile_photos(user_id,mime,image) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET mime=excluded.mime,image=excluded.image',(user['user_id'],mime,raw))
                    elif body.get('removePhoto'):c.execute('DELETE FROM profile_photos WHERE user_id=?',(user['user_id'],))
                    c.commit()
                return self.send_json({'ok':True})
            except Exception as e:return self.send_error_json(str(e),400)
        if urlparse(self.path).path!='/api/workspace':return self.send_error_json('Not found',404)
        shop=self.api_shop()
        if not shop:return self.send_error_json('Sign in required',401)
        try:return self.send_json({'workspace':replace_workspace(shop,self.json_body())})
        except RuntimeError as e:return self.send_error_json(str(e),409)
        except sqlite3.IntegrityError as e:return self.send_error_json('A product SKU, barcode, or record ID is already in use.',409)
        except Exception as e:return self.send_error_json(str(e),400)
    def do_DELETE(self):
        if urlparse(self.path).path!='/api/session':return self.send_error_json('Not found',404)
        raw=self.cookies.get(SESSION_COOKIE)
        if raw:
            with db() as c:c.execute('DELETE FROM sessions WHERE token_hash=?',(hashlib.sha256(raw.encode()).hexdigest(),))
        return self.send_json({'ok':True},headers={'Set-Cookie':self.session_cookie('',0)})
    def record_sale(self,shop,body):
        rid=str(body.get('requestId') or uuid.uuid4());sale=body.get('sale') or {};items=sale.get('items') or []
        if not items:return self.send_error_json('Add at least one product.')
        try:
            with db() as c:
                c.execute('BEGIN IMMEDIATE');old=c.execute('SELECT result FROM idempotency WHERE shop_id=? AND request_id=?',(shop,rid)).fetchone()
                if old:c.rollback();return self.send_json({'workspace':load_workspace(shop),'saleId':json.loads(old['result'])['saleId'],'duplicate':True})
                lines=[];total=0;cost=0;count=0
                for item in items:
                    qty=int(item.get('qty') or 0);p=c.execute('SELECT * FROM products WHERE shop_id=? AND product_id=? AND archived=0',(shop,str(item.get('productId') or ''))).fetchone()
                    if not p or qty<1:raise ValueError('Choose a valid product and quantity.')
                    changed=c.execute('UPDATE products SET stock=stock-?,sold=sold+? WHERE shop_id=? AND product_id=? AND stock>=?',(qty,qty,shop,p['product_id'],qty))
                    if changed.rowcount!=1:raise ValueError(f"Not enough stock for {p['name']}.")
                    lines.append((p,qty));total+=p['price']*qty;cost+=p['cost']*qty;count+=qty
                total=max(0,total-float(sale.get('discount') or 0));customer_id=sale.get('customerId') or None;customer_name=''
                if sale.get('method')=='Credit':
                    customer=c.execute('SELECT * FROM customers WHERE shop_id=? AND customer_id=?',(shop,customer_id)).fetchone()
                    if not customer:raise ValueError('Choose a customer for a credit sale.')
                    customer_name=customer['name'];c.execute('UPDATE customers SET balance=balance+?,due=CASE WHEN due=\'\' THEN ? ELSE due END WHERE shop_id=? AND customer_id=?',(total,sale.get('date',date.today().isoformat()),shop,customer_id))
                elif customer_id:
                    cu=c.execute('SELECT name FROM customers WHERE shop_id=? AND customer_id=?',(shop,customer_id)).fetchone();customer_name=cu['name'] if cu else ''
                sid=str(sale.get('id') or 'S-'+uuid.uuid4().hex[:10]);sdate=str(sale.get('date') or date.today().isoformat())
                c.execute("INSERT INTO sales(sale_id,shop_id,sale_date,total,cost,method,customer_name,customer_id,item_count,status) VALUES(?,?,?,?,?,?,?,?,?,'confirmed')",(sid,shop,sdate,total,cost,str(sale.get('method') or 'Cash'),customer_name,customer_id,count))
                for p,qty in lines:c.execute('INSERT INTO sale_items(shop_id,sale_id,product_id,product_name,quantity,unit_price,unit_cost) VALUES(?,?,?,?,?,?,?)',(shop,sid,p['product_id'],p['name'],qty,p['price'],p['cost']))
                c.executemany('INSERT INTO inventory_movements VALUES(?,?,?,?,?,?,?,?,?,?)',[(f'{sid}-{p["product_id"]}',shop,p['product_id'],'sale',-qty,sdate,sid,'','','Confirmed sale') for p,qty in lines])
                c.execute('INSERT INTO idempotency VALUES(?,?,?,?)',(shop,rid,json.dumps({'saleId':sid}),int(time.time())));c.execute('UPDATE shops SET revision=revision+1 WHERE shop_id=?',(shop,));c.commit()
            return self.send_json({'workspace':load_workspace(shop),'saleId':sid})
        except (ValueError,sqlite3.IntegrityError) as e:
            try:c.rollback()
            except:pass
            return self.send_error_json(str(e),409)
        except Exception as e:
            try:c.rollback()
            except:pass
            return self.send_error_json('Sale could not be saved.',500)
    def create_purchase_draft(self,shop,body):
        rid=str(body.get('requestId') or uuid.uuid4());order=body.get('purchase') or {};items=body.get('items') or [order]
        if not items:return self.send_error_json('Add at least one product to the draft.')
        try:
            with db() as c:
                c.execute('BEGIN IMMEDIATE');old=c.execute('SELECT result FROM idempotency WHERE shop_id=? AND request_id=?',(shop,rid)).fetchone()
                if old:c.rollback();return self.send_json({'workspace':load_workspace(shop),'draftId':json.loads(old['result'])['draftId'],'duplicate':True})
                ids=[]
                for index,item in enumerate(items):
                    qty=int(item.get('qty') or order.get('qty') or 0);pid=str(item.get('productId') or order.get('productId') or '')
                    product=c.execute('SELECT * FROM products WHERE shop_id=? AND product_id=? AND archived=0',(shop,pid)).fetchone()
                    if not product or qty<1:raise ValueError('Choose a valid product and quantity for every draft line.')
                    draft_id=str(item.get('id') or f'{rid}-{index+1}')
                    cost=float(item.get('cost') or product['cost'])
                    c.execute("INSERT INTO purchases(purchase_id,shop_id,supplier,product,product_id,quantity,cost,purchase_date,invoice,status,batch,expiry) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(draft_id,shop,str(item.get('supplier') or order.get('supplier') or 'Supplier to confirm'),product['name'],pid,qty,cost,str(item.get('date') or date.today().isoformat()),str(item.get('invoice') or ''),'draft',str(item.get('batch') or ''),str(item.get('expiry') or '')))
                    ids.append(draft_id)
                c.execute('INSERT INTO idempotency VALUES(?,?,?,?)',(shop,rid,json.dumps({'draftId':ids[0],'draftIds':ids}),int(time.time())));c.execute('UPDATE shops SET revision=revision+1 WHERE shop_id=?',(shop,));c.commit()
            return self.send_json({'workspace':load_workspace(shop),'draftId':ids[0]})
        except (ValueError,sqlite3.IntegrityError) as e:
            try:c.rollback()
            except:pass
            return self.send_error_json(str(e),409)
        except Exception:
            try:c.rollback()
            except:pass
            return self.send_error_json('Purchase draft could not be saved.',500)
    def record_return(self,shop,body):
        rid=str(body.get('requestId') or uuid.uuid4());sale_id=str(body.get('saleId') or '');items=body.get('items') or []
        if not sale_id or not items:return self.send_error_json('Choose a completed sale and returned products.')
        try:
            with db() as c:
                c.execute('BEGIN IMMEDIATE');old=c.execute('SELECT result FROM idempotency WHERE shop_id=? AND request_id=?',(shop,rid)).fetchone()
                if old:c.rollback();return self.send_json({'workspace':load_workspace(shop),'returnId':json.loads(old['result'])['returnId'],'duplicate':True})
                sale=c.execute("SELECT * FROM sales WHERE shop_id=? AND sale_id=? AND status='confirmed'",(shop,sale_id)).fetchone()
                if not sale:raise ValueError('The confirmed sale could not be found.')
                amount=0
                for item in items:
                    qty=int(item.get('qty') or 0);pid=str(item.get('productId') or '')
                    line=c.execute('SELECT * FROM sale_items WHERE shop_id=? AND sale_id=? AND product_id=?',(shop,sale_id,pid)).fetchone()
                    if not line or qty<1 or line['returned_quantity']+qty>line['quantity']:raise ValueError('Return quantity is greater than the unreturned amount.')
                    c.execute('UPDATE sale_items SET returned_quantity=returned_quantity+? WHERE line_id=?',(qty,line['line_id']))
                    c.execute('UPDATE products SET stock=stock+?,sold=MAX(0,sold-?) WHERE shop_id=? AND product_id=?',(qty,qty,shop,pid))
                    c.execute('INSERT INTO inventory_movements VALUES(?,?,?,?,?,?,?,?,?,?)',(f'{rid}-{pid}',shop,pid,'return',qty,date.today().isoformat(),sale_id,'','','Returned item from confirmed sale'))
                    amount+=line['unit_price']*qty
                return_id='R-'+uuid.uuid4().hex[:10]
                c.execute('INSERT INTO returns VALUES(?,?,?,?,?,?)',(return_id,shop,sale_id,date.today().isoformat(),amount,str(body.get('reason') or 'Customer return')))
                c.execute('INSERT INTO idempotency VALUES(?,?,?,?)',(shop,rid,json.dumps({'returnId':return_id}),int(time.time())));c.execute('UPDATE shops SET revision=revision+1 WHERE shop_id=?',(shop,));c.commit()
            return self.send_json({'workspace':load_workspace(shop),'returnId':return_id,'refundAmount':amount})
        except (ValueError,sqlite3.IntegrityError) as e:
            try:c.rollback()
            except:pass
            return self.send_error_json(str(e),409)
        except Exception:
            try:c.rollback()
            except:pass
            return self.send_error_json('Return could not be saved.',500)
    def receive_stock(self,shop,body):
        rid=str(body.get('requestId') or uuid.uuid4());purchase=body.get('purchase') or {};items=body.get('items') or [purchase]
        if not items:return self.send_error_json('Add at least one product to receive.')
        try:
            with db() as c:
                c.execute('BEGIN IMMEDIATE');old=c.execute('SELECT result FROM idempotency WHERE shop_id=? AND request_id=?',(shop,rid)).fetchone()
                if old:c.rollback();return self.send_json({'workspace':load_workspace(shop),'purchaseId':json.loads(old['result'])['purchaseId'],'duplicate':True})
                purchase_ids=[]
                for index,line in enumerate(items):
                    qty=int(line.get('qty') or 0);pid=str(line.get('productId') or '')
                    if qty<1:raise ValueError('Enter a quantity greater than zero for every product.')
                    product=c.execute('SELECT * FROM products WHERE shop_id=? AND product_id=? AND archived=0',(shop,pid)).fetchone()
                    if not product:raise ValueError('Choose a valid product for every line.')
                    draft_id=str(line.get('draftId') or '')
                    if draft_id:
                        draft=c.execute("SELECT * FROM purchases WHERE shop_id=? AND purchase_id=? AND status='draft'",(shop,draft_id)).fetchone()
                        if not draft or draft['product_id']!=pid or qty>draft['quantity']:raise ValueError('That draft order does not match the product or received quantity.')
                        remaining=draft['quantity']-qty
                        if remaining:c.execute("UPDATE purchases SET quantity=? WHERE shop_id=? AND purchase_id=?",(remaining,shop,draft_id))
                        else:c.execute("DELETE FROM purchases WHERE shop_id=? AND purchase_id=?",(shop,draft_id))
                    cost=float(line.get('cost') or product['cost']);batch=str(line.get('batch') or '');expiry=str(line.get('expiry') or '')
                    c.execute('UPDATE products SET stock=stock+?,cost=?,expiry=CASE WHEN ?<>? THEN ? ELSE expiry END WHERE shop_id=? AND product_id=?',(qty,cost,expiry,'',expiry,shop,pid))
                    purchase_id=str(line.get('id') or (purchase.get('id') if len(items)==1 else f'{rid}-{index+1}') or 'P-'+uuid.uuid4().hex[:10])
                    supplier=str(line.get('supplier') or purchase.get('supplier') or 'Supplier');invoice=str(line.get('invoice') or purchase.get('invoice') or '')
                    purchase_date=str(line.get('date') or purchase.get('date') or date.today().isoformat())
                    c.execute("INSERT INTO purchases(purchase_id,shop_id,supplier,product,product_id,quantity,cost,purchase_date,invoice,status,batch,expiry) VALUES(?,?,?,?,?,?,?,?,?,'received',?,?)",(purchase_id,shop,supplier,product['name'],pid,qty,cost,purchase_date,invoice,batch,expiry))
                    c.execute('INSERT INTO inventory_movements VALUES(?,?,?,?,?,?,?,?,?,?)',(f'{rid}-{index+1}',shop,pid,'stock-in',qty,purchase_date,purchase_id,batch,expiry,'Confirmed stock receipt'))
                    purchase_ids.append(purchase_id)
                c.execute('INSERT INTO idempotency VALUES(?,?,?,?)',(shop,rid,json.dumps({'purchaseId':purchase_ids[0],'purchaseIds':purchase_ids}),int(time.time())));c.execute('UPDATE shops SET revision=revision+1 WHERE shop_id=?',(shop,));c.commit()
            return self.send_json({'workspace':load_workspace(shop),'purchaseId':purchase_ids[0],'purchaseIds':purchase_ids})
        except (ValueError,sqlite3.IntegrityError) as e:
            try:c.rollback()
            except:pass
            return self.send_error_json(str(e),409)
        except Exception:
            try:c.rollback()
            except:pass
            return self.send_error_json('Stock receipt could not be saved.',500)

if __name__=='__main__':
    init_db()
    print(f'ShelfWise running at http://localhost:{PORT}/ (bound to {HOST}, database: {DB_PATH})')
    ThreadingHTTPServer((HOST,PORT),Handler).serve_forever()
