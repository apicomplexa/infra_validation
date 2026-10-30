"""Every diagram of docs/workflow.qmd, written as includable fragments.

    python docs/_workflow/diagrams.py        # rewrites docs/_workflow/<name>.md

Coordinates are set by hand on purpose: the layout is the diagram's argument
(what feeds what, nothing crossing), which an automatic layout does not keep.
A line too long for its box is reported, and the script exits non-zero.
"""
import sys
from pathlib import Path

from svgkit import ERRORS, Diagram, ladder

D = {}


def new(name, w, label):
    d = Diagram(name, w, label)
    D[name] = d
    return d


# ---------------------------------------------------------------- cycle
d = new("cycle", 960, "Рабочий цикл: офлайн-прогон, дочитка Яндекса, повторный прогон")
w1 = d.box(16, 20, 200, ["!pixi run workflow", "офлайн; Яндекс судится", "по текущему кэшу"], "check")
q = d.box(256, 26, 210, ["?«Не запрошено, хотя вердикт", "?зависит от Яндекса» > 0?"], "q")
f = d.box(506, 20, 210, ["!pixi run yandex-fetch", "сеть; дописывает кэш", "`resources/cache/yandex/"], "check")
w2 = d.box(756, 20, 188, ["!pixi run workflow", "пересчёт: check_yandex", "и всё, что ниже"], "check")
ok = d.box(756, 132, 188, ["!results/ готовы"], "ok")
d.arrow([w1.r(), (q.x, w1.cy)])
d.arrow([q.r(), (f.x, q.cy)], "да")
d.arrow([f.r(), (w2.x, f.cy)])
d.arrow([w2.b(), ok.t()])
d.arrow([q.b(), (q.cx, ok.cy), ok.l()], "нет", at=(q.cx + 6, ok.cy - 26))

# ---------------------------------------------------------------- inputs
d = new("inputs", 1080, "Что читает каждая категория")
cols = [
    ("Общепит · catering", [
        (["!data/raw/final/*общепит.xlsx ×3", "листы data, original_data, quality;", "в data — флаги OSM, GIS, Arkh_admin", "и проверка подрядчика по Яндексу"], "data"),
        (["!Реестр ФСРАР (ZIP)", "источник: своя действующая", "лицензия общепита"], "data"),
        (["!Overture Places (parquet)"], "data"),
        (["!FSQ OS Places (parquet)"], "data"),
        (["!Кэш Яндекса", "`final_yandex_cache.jsonl"], "data"),
    ]),
    ("Алкоголь · alcohol", [
        (["!data/raw/v4/*алкоголь.csv", "name_orig, adress_orig, type_orig,", "providers, matched_source_ids", "→ trade, evidence"], "data"),
        (["!Реестр ФСРАР (ZIP)", "источник, только если своя", "действующая и имя совпало"], "data"),
        (["!Overture Places (parquet)"], "data"),
        (["!FSQ OS Places (parquet)"], "data"),
        (["!Кэш Яндекса", "`v4_yandex_cache.jsonl"], "data"),
    ]),
    ("Табак · tobacco", [
        (["!data/raw/v4/*табачное изделие.csv", "те же колонки, что у алкоголя;", "сверка с ЕГАИС — вместе", "с алкоголем (группа v4)"], "data"),
        (["!Реестр ФСРАР (ZIP)", "не источник: только косвенный", "довод после « || »"], "note"),
        (["!Overture Places (parquet)"], "data"),
        (["!FSQ OS Places (parquet)"], "data"),
        (["!Кэш Яндекса", "`v4_yandex_cache.jsonl"], "data"),
    ]),
]
for i, (title, items) in enumerate(cols):
    x = 16 + i * 356
    y = 44
    for lines, kind in items:
        b = d.box(x + 14, y, 300, lines, kind)
        y = b.bottom + 10
    d.group(x, 14, 328, y - 4, title, "cat")

# ---------------------------------------------------------------- general DAG
d = new("dag", 1180, "Правила Snakemake и файлы между ними для одной поставки")
raw = d.box(16, 40, 180, ["!data/raw/…", "XLSX или CSV поставки"], "data")
snap = d.box(16, 170, 180, ["!Overture, FSQ", "parquet-снимки;", "кэш Яндекса (jsonl)"], "data")
zipb = d.box(16, 330, 180, ["!Реестр ФСРАР", "ZIP, 10,9 ГБ XML"], "data")
obj = d.box(250, 40, 170, ["!objects", "адаптер входа:", "строки → объекты;", "+ rowmap → decide"], "check")
reg = d.box(250, 330, 170, ["!egais_register", "потоковый разбор;", "один на прогон"], "check")
d.group(470, 112, 220, 190, "по правилу на проверку", "check")
chk = d.box(484, 140, 192, ["!check_providers", "!check_arkh_admin", "!check_export_name", "!check_yandex", "!check_overture", "!check_foursquare"], "check")
grp = d.box(470, 330, 220, ["!egais_group", "все поставки match_group", "разом → licmatch"], "lm", href="sec-egais")
spl = d.box(470, 430, 220, ["!check_egais", "строки своей поставки"], "check")
ev = d.box(740, 140, 150, ["!Evidence", "объект × источник;", "по файлу", "на проверку", "", "`evidence/", "`  <check>.parquet"], "data", h=345)
dec = d.box(930, 140, 220, ["!decide", "правило категории:", "Подтверждено, Вид?,", "Основания"], "cat", href="sec-metrics-rule")
dlv = d.box(930, 290, 220, ["!deliver", "копия выгрузки:", "…_подтверждено.xlsx"], "ok")
sm = d.box(930, 420, 220, ["!summary_category", "<категория>_summary.txt,", "<категория>_objects.csv"], "ok")
d.arrow([raw.r(), (obj.x, raw.cy)])
d.arrow([obj.r(), (450, obj.cy), (450, chk.y + 30), (chk.x, chk.y + 30)], "objects.parquet", at=(430, obj.cy - 6), anchor="start")
d.arrow([(450, chk.y + 30), (450, grp.y + 16), (grp.x, grp.y + 16)])
d.arrow([snap.r(), (chk.x, snap.cy)], "снимки и кэш", at=(220, snap.cy - 6))
d.arrow([zipb.r(), (reg.x, zipb.cy)])
d.arrow([reg.r(dy=8), (grp.x, reg.cy + 8)], "registry.tsv", at=(426, reg.cy + 2 - 12))
d.arrow([grp.b(), spl.t()], "groups/{g}.parquet")
d.arrow([chk.r(), (ev.x, chk.cy)], "пишет")
d.arrow([spl.r(), (715, spl.cy), (715, 455), (ev.x, 455)])
d.arrow([(ev.right, dec.cy), (dec.x, dec.cy)])
d.arrow([dec.b(), dlv.t()], "results/{d}/objects.parquet", at=(dec.cx + 6, (dec.bottom + dlv.y) / 2 + 4))
d.arrow([(dlv.right - 30, dlv.bottom), (dlv.right - 30, sm.y)], "+ summary.txt", at=(dlv.right - 36, (dlv.bottom + sm.y) / 2 + 4), anchor="end")
d.arrow([raw.t(), (raw.cx, 18), (1166, 18), (1166, dlv.cy), (dlv.right, dlv.cy)], "исходный файл: копируется, не меняется", at=(560, 13))

# ---------------------------------------------------------------- catering DAG
d = new("dag_catering", 1100, "DAG общепита: три поставки, у каждой своя группа ЕГАИС")
xl = d.box(16, 70, 184, ["!XLSX поставки", "по одному на город:", "Архангельск, Новодвинск,", "Северодвинск"], "data")
zp = d.box(16, 250, 170, ["!Реестр ФСРАР", "ZIP"], "data")
ob = d.box(226, 70, 140, ["!objects", "2 500 строк →", "1 141 объект", "(Архангельск)"], "check")
rg = d.box(226, 250, 140, ["!egais_register", "один на прогон"], "check")
d.group(400, 40, 680, 290, "одна поставка; так же для каждой из трёх (своя match_group)", "check", stack=2)
ck = d.box(420, 70, 200, ["!6 проверок", "providers · arkh_admin", "export_name · yandex", "overture · foursquare"], "check")
eg = d.box(420, 230, 200, ["!egais_group", "группа = эта поставка;", "licmatch, режим catering"], "lm", href="sec-egais-catering")
ce = d.box(650, 230, 150, ["!check_egais"], "check")
dc = d.box(680, 100, 150, ["!decide", "7 групп, N = 2"], "cat", href="sec-metrics-catering")
dv = d.box(870, 100, 190, ["!deliver", "…_подтверждено.xlsx"], "ok")
sc = d.box(820, 370, 260, ["!summary_category catering", "catering_summary.txt,", "catering_objects.csv"], "ok")
d.arrow([xl.r(), (ob.x, xl.cy)])
d.arrow([ob.r(dy=-10), (ck.x, ob.cy - 10)], "objects")
d.arrow([ob.r(dy=20), (390, ob.cy + 20), (390, eg.y + 14), (eg.x, eg.y + 14)])
d.arrow([zp.r(), (rg.x, zp.cy)])
d.arrow([rg.r(), (eg.x, rg.cy)], "registry.tsv", at=(372, rg.cy + 14), anchor="start")
d.arrow([eg.r(), (ce.x, eg.r()[1])])
d.arrow([ck.r(), (dc.x, ck.r()[1])], "evidence ×6")
d.arrow([ce.t(), (ce.cx, dc.bottom)], "evidence")
d.arrow([dc.r(), dv.l()])
d.arrow([(dc.cx + 30, dc.bottom), (dc.cx + 30, 300), (sc.x + 60, 300), (sc.x + 60, sc.y)], "×3 поставки", at=(sc.x + 66, 355))

# ---------------------------------------------------------------- v4 DAG
d = new("dag_v4", 1100, "DAG алкоголя и табака: одна сверка с ЕГАИС на оба слоя")
tc = d.box(16, 30, 170, ["!CSV табака", "749 объектов"], "data")
ac = d.box(16, 300, 170, ["!CSV алкоголя", "945 объектов"], "data")
rt = d.box(16, 165, 170, ["!registry.tsv", "Архангельск"], "data")
to = d.box(220, 30, 120, ["!objects"], "check")
ao = d.box(220, 300, 120, ["!objects"], "check")
tk = d.box(380, 30, 210, ["!5 проверок", "providers · export_name", "yandex · overture · foursquare"], "check")
ak = d.box(380, 300, 210, ["!5 проверок", "providers · export_name", "yandex · overture · foursquare"], "check")
g = d.box(380, 150, 210, ["!egais_group v4", "concat(табак, алкоголь)", "→ licmatch.match один раз"], "lm", href="sec-egais-v4")
te = d.box(800, 120, 140, ["!check_egais", "строки табака"], "check")
ae = d.box(800, 215, 140, ["!check_egais", "строки алкоголя"], "check")
td = d.box(800, 30, 140, ["!decide", "egais: never_count"], "cat", href="sec-metrics-tobacco")
ad = d.box(800, 300, 140, ["!decide", "ЕГАИС — источник"], "cat", href="sec-metrics-alcohol")
tv = d.box(970, 30, 114, ["!deliver", "XLSX, сводка", "tobacco_*"], "ok")
av = d.box(970, 300, 114, ["!deliver", "XLSX, сводка", "alcohol_*"], "ok")
for c, o, k, dd, v in ((tc, to, tk, td, tv), (ac, ao, ak, ad, av)):
    d.arrow([c.r(), (o.x, c.cy)])
    d.arrow([o.r(), (k.x, o.cy)])
    d.arrow([k.r(), (dd.x, k.cy)], "evidence ×5")
    d.arrow([dd.r(), (v.x, dd.cy)])
d.arrow([to.b(), (to.cx, g.y + 16), (g.x, g.y + 16)], "1 · табак первым", at=(to.cx + 6, to.bottom + 30))
d.arrow([ao.t(), (ao.cx, g.bottom - 16), (g.x, g.bottom - 16)], "2 · алкоголь", at=(ao.cx + 6, ao.y - 26))
d.arrow([rt.r(), (g.x, rt.cy)])
d.arrow([g.r(), (700, g.cy), (700, te.cy), (te.x, te.cy)], "groups/v4.parquet", at=(604, g.cy - 6), anchor="start")
d.arrow([(700, g.cy), (700, ae.cy), (ae.x, ae.cy)])
d.arrow([te.t(), (te.cx, td.bottom)])
d.arrow([ae.b(), (ae.cx, ad.y)])

# ---------------------------------------------------------------- yandex queue DAG
d = new("dag_yandex", 1080, "Цели вне all: yandex_queue и yandex_fetch")
a = d.box(16, 30, 200, ["!objects.parquet", "evidence/*.parquet", "кроме yandex"], "data")
q1 = d.box(266, 30, 170, ["!yandex_queue", "base.needs_query()"], "check", href="sec-yandex-queue")
f1 = d.box(496, 30, 190, ["!yandex_fetch", "по поставкам по очереди;", "сбой не кэшируется"], "check", href="sec-yandex-fetch")
c1 = d.box(736, 30, 160, ["!Кэш Яндекса", "jsonl, только", "дописывается"], "data")
cy = d.box(936, 30, 128, ["!check_yandex", "следующий", "workflow"], "check")
d.arrow([a.r(), (q1.x, a.cy)])
d.arrow([q1.r(), (f1.x, q1.cy)], "yandex_queue.txt")
d.arrow([f1.r(), (c1.x, f1.cy)], "дописывает")
d.arrow([c1.r(), (cy.x, c1.cy)], "читает")

# ---------------------------------------------------------------- entities
d = new("entities", 1080, "Таблицы, которыми обмениваются слои, и их ключи")
rw = d.box(16, 30, 190, ["!Строки поставки", "`row (позиция)", "`…колонки подрядчика"], "data")
rm = d.box(16, 200, 190, ["!RowMap", "`row", "`object_id", "`entity"], "data")
ot = d.box(270, 30, 240, ["!ObjectTable", "одна строка = объект", "`object_id = delivery:entity", "`delivery, city, name", "`address, lat, lon", "`site_key, category", "`generic, n_rows", "`+ колонки адаптера"], "data")
rt = d.box(270, 290, 240, ["!RegisterTable", "одна строка = запись реестра", "`record_id, holder_id", "`holder_name, venue_name", "`street, house, block", "`lat, lon, in_force, kind"], "data")
ev = d.box(570, 30, 230, ["!Evidence", "одна строка = объект × источник", "`object_id, check, source", "`origin", "`identity · record_state", "`kind_text · kind_scheme", "`matched_name, distance_m", "`name_score, ref, label, text"], "data")
mr = d.box(570, 290, 230, ["!MatchResult.table", "одна строка = объект", "`status, record, failed_rule", "`score, addr, name, distance", "`name_basis, unit", "`claimed_by, margin"], "lm", href="sec-licmatch")
dt = d.box(860, 30, 204, ["!Решённая таблица", "одна строка = объект", "`ObjectTable + wide()", "`src_*, n_sources, sources", "`confirmed, reasons", "`<вид>, <вид>_reasons", "`yandex_how, needs_yandex"], "cat")
d.arrow([rw.r(), (ot.x, rw.cy)], "группировка")
d.arrow([rw.b(), rm.t()], "адаптер")
d.arrow([ot.r(dy=-20), (ev.x, ot.cy - 20)], "checks/*")
d.arrow([ot.r(dy=50), (540, ot.cy + 50), (540, mr.y + 20), (mr.x, mr.y + 20)], "licmatch.match", at=(546, mr.y - 16))
d.arrow([rt.r(dy=20), (mr.x, rt.cy + 20)])
d.arrow([mr.t(), (mr.cx, ev.bottom)], "checks/egais")
d.arrow([ev.r(), (dt.x, ev.r()[1])], "decide")
d.arrow([dt.b(), (dt.cx, 470), (rm.cx, 470), rm.b()], "to_rows: вердикт пишется во все строки объекта", at=(420, 465), dashed=True)

# ---------------------------------------------------------------- category rule
d = new("rule", 1080, "Evidence → Подтверждено, Вид?, Основания (categories/base.py)")
top = d.box(16, 16, 1048, ["!Evidence всех проверок категории", "registry.stack: строки всех проверок, в порядке checks"], "data")
X = [16, 400, 760]
for x, t in zip(X, ["Существование", "Вид", "Основания"]):
    d.text(x + 4, 104, t.upper(), cls="g")
d.arrow([(X[0] + 131, top.bottom), (X[0] + 131, 116)])
d.arrow([(X[1] + 150, top.bottom), (X[1] + 150, 116)])
d.arrow([(X[2] + 152, top.bottom), (X[2] + 152, 116)])
ex = ladder(d, X[0], 118, 262, [
    ("q", ["?identity ∈ count.identity и", "?record_state ∈ count.record_state?"], None, None, None, "да"),
    ("q", ["?source в группе независимости", "?и не в never_count?"], None, None, None, "да"),
    ("q", ["?exclude: запись другой группы?", "?(Overture, собранный из одного FSQ)"], None, None, None, "нет"),
    ("box", ["!src_<группа> = True", "один раз на группу, сколько бы", "строк ни было"], "cat"),
    ("box", ["!n_sources = Σ src_*", "sources = метки групп через «; »"], "cat"),
    ("box", ["!Подтверждено = n_sources ≥ 2"], "ok"),
], 0, 0)
d.text(X[0] + 268, ex[0].cy + 4, "нет → после « || »")
d.text(X[0] + 268, ex[1].cy + 4, "нет → после « || »")
d.text(X[0] + 268, ex[2].cy + 4, "да → молча")
kd = ladder(d, X[1], 118, 300, [
    ("q", ["?identity ∈ kind.vote_identity?"], None, None, None, "да"),
    ("box", ["!Голоса по kind.votes", "строки своей kind_scheme", "(и checks, record_state, если заданы);", "пустой kind_text — только keep_empty"], "cat"),
    ("box", ["!classify(kind_text, scheme)", "yes / no / vending / ''"], "ref", "sec-kind"),
    ("box", ["!fold_votes → «Вид?»", "да / нет / противоречие /", "автомат / нет данных"], "ok"),
    ("box", ["!«Вид: основания»", "«МЕТКА: «текст» → слово» через « | »"], "cat"),
], 0, 0)
rs = d.box(X[2], 118, 304, ["!Засчитанные строки", "по группам в порядке groups; внутри", "группы — по group.checks:", "«МЕТКА: текст; текст»"], "cat")
rc = d.box(X[2], rs.bottom + 24, 304, ["!Не засчитанные строки с текстом", "кроме молча исключённых exclude;", "в порядке con_order:", "«метка: текст»"], "cat")
ro = d.box(X[2], rc.bottom + 24, 304, ["!Основания", "засчитанные через « | »", "(или «источников нет»)", "+ « || против/не найдено: »", "+ не засчитанные через «; »"], "ok")
d.arrow([rs.b(), rc.t()])
d.arrow([rc.b(), ro.t()])


# ---------------------------------------------------------------- metrics: vote | check | group
def metrics(name, label, rows, groups, votes, kind_word, con_note):
    """rows: (key, lines, kind, href); groups: (label lines, [row keys], edge label per key);
    votes: (label lines, row key, edge label)."""
    d = new(name, 1180, label)
    CX, CW = 400, 330      # checks
    GX, GW = 790, 220      # groups
    VX, VW = 160, 190      # votes
    d.text(CX, 22, "ПРОВЕРКА → СТРОКИ EVIDENCE", cls="g")
    d.text(GX, 22, "ГРУППЫ «ПОДТВЕРЖДЕНО»", cls="g")
    d.text(VX, 22, "ГОЛОСА «ВИД?»", cls="g")
    y, box = 36, {}
    for key, lines, kind, href in rows:
        box[key] = d.box(CX, y, CW, lines, kind, href=href)
        y = box[key].bottom + 12
    gb = []
    for glines, keys, labels in groups:
        top_, bot = box[keys[0]].y, box[keys[-1]].bottom
        g = d.box(GX, top_, GW, glines, "cat", h=bot - top_)
        gb.append(g)
        for k, lab in zip(keys, labels):
            if lab is not None:
                d.arrow([box[k].r(), (GX, box[k].cy)], lab)
    vb = []
    for vlines, key, lab in votes:
        b = box[key]
        v = d.box(VX, b.cy - 22, VW, vlines, "ref", href="sec-kind", h=44)
        vb.append(v)
        d.arrow([b.l(), (v.right, b.cy)], lab)
    yo = d.box(1036, 36, 132, ["!Подтверждено", "n_sources ≥ 2"], "ok", h=y - 48)
    for g in gb:
        d.arrow([g.r(), (yo.x, g.cy)])
    ky = vb[0].y
    ko = d.box(16, ky, 110, ["!" + kind_word, "fold_votes"], "ok", h=vb[-1].bottom - ky)
    for v in vb:
        d.arrow([v.l(), (ko.right, v.cy)])
    d.text(CX, y + 14, con_note)
    return d


metrics("m_catering", "Общепит: какие строки Evidence куда идут", [
    ("prov", ["!providers: флаг OSM / GIS выгрузки", "strong/current"], "check", "sec-providers"),
    ("rel", ["!relink: провайдер соседней записи", "strong/current"], "check", "sec-providers"),
    ("arkh", ["!arkh_admin: Arkh_admin = 1", "strong/current"], "check", "sec-arkh"),
    ("exp", ["!export_name: описание в названии", "source = export — не считается"], "check", "sec-export"),
    ("yc", ["!yandex_contractor: «Найдено»", "имя совпало — strong/current", "~только адрес — weak → после « || »"], "check", "sec-yandex-contractor"),
    ("yo", ["!yandex: наша проверка", "present — strong/current", "~closed, ambiguous, absent → после « || »"], "check", "sec-yandex-own"),
    ("ov", ["!overture", "strong/current", "~досталась соседу — weak"], "check", "sec-overture"),
    ("fs", ["!foursquare", "свежая и открытая — strong/current", "~stale, closed, соседу — после « || »"], "check", "sec-foursquare"),
    ("eg", ["!egais, режим catering", "own — strong/current", "~inactive, taken, чужая… → после « || »"], "check", "sec-egais-catering"),
], [
    (["!OSM · src_osm", "!2GIS · src_gis", "по флагу провайдера"], ["prov", "rel"], ["флаг", "одолжен"]),
    (["!Администрация", "src_arkh"], ["arkh"], [""]),
    (["!Яндекс · src_yandex", "одна группа: сначала", "наша проверка"], ["yc", "yo"], ["strong", "present"]),
    (["!Overture · src_overture", "кроме места из одного FSQ"], ["ov"], ["strong"]),
    (["!Foursquare · src_fsq"], ["fs"], ["current"]),
    (["!ЕГАИС · src_egais"], ["eg"], ["own"]),
], [
    (["!ru_text", "описание в названии"], "exp", ""),
    (["!yandex_rubric", "рубрика карточки"], "yo", ""),
    (["!overture_category"], "ov", "и weak"),
    (["!fsq_category", "в т.ч. stale"], "fs", "и weak"),
], "Общепит?", "Строки, не попавшие в группы, с непустым текстом → «Основания» после « || », по con_order.")

metrics("m_alcohol", "Алкоголь: ЕГАИС — источник и голос вида, только при совпадении имени", [
    ("prov", ["!providers: колонка providers CSV", "strong/current"], "check", "sec-providers"),
    ("rel", ["!relink: провайдер соседней записи", "strong/current"], "check", "sec-providers"),
    ("exp", ["!export_name: описание в названии", "source = export — не считается"], "check", "sec-export"),
    ("yo", ["!yandex: наша проверка", "(проверки подрядчика в CSV нет)", "present — strong/current"], "check", "sec-yandex-own"),
    ("ov", ["!overture", "strong/current"], "check", "sec-overture"),
    ("fs", ["!foursquare", "свежая и открытая — strong/current"], "check", "sec-foursquare"),
    ("eg", ["!egais, режим v4", "своя действующая и имя совпало — strong", "~без имени, архивная, чужая → после « || »"], "check", "sec-egais-v4"),
], [
    (["!OSM · src_osm", "!2GIS · src_gis"], ["prov", "rel"], ["флаг", "одолжен"]),
    (["!Яндекс · src_yandex"], ["yo"], ["present"]),
    (["!Overture · src_overture"], ["ov"], ["strong"]),
    (["!Foursquare · src_fsq"], ["fs"], ["current"]),
    (["!ЕГАИС · src_egais"], ["eg"], ["имя"]),
], [
    (["!ru_text", "описание в названии"], "exp", ""),
    (["!yandex_rubric", "рубрика карточки"], "yo", ""),
    (["!overture_category"], "ov", ""),
    (["!fsq_category"], "fs", ""),
    (["!egais_activity", "своя действующая → да"], "eg", "strong"),
], "Алкоголь?", "Голосуют только strong. Не засчитанное с текстом → после « || »: yandex, egais, overture, foursquare.")

metrics("m_tobacco", "Табак: ЕГАИС работает, но не источник и не голос", [
    ("prov", ["!providers: колонка providers CSV", "strong/current"], "check", "sec-providers"),
    ("rel", ["!relink: провайдер соседней записи", "strong/current"], "check", "sec-providers"),
    ("exp", ["!export_name: описание в названии", "source = export — не считается"], "check", "sec-export"),
    ("yo", ["!yandex: наша проверка", "present — strong/current"], "check", "sec-yandex-own"),
    ("ov", ["!overture", "strong/current"], "check", "sec-overture"),
    ("fs", ["!foursquare", "свежая и открытая — strong/current"], "check", "sec-foursquare"),
    ("eg", ["!egais, режим v4 (общая сверка)", "never_count: не группа и не голос", "~probable / weak → только после « || »"], "note", "sec-egais-v4"),
], [
    (["!OSM · src_osm", "!2GIS · src_gis"], ["prov", "rel"], ["флаг", "одолжен"]),
    (["!Яндекс · src_yandex"], ["yo"], ["present"]),
    (["!Overture · src_overture"], ["ov"], ["strong"]),
    (["!Foursquare · src_fsq"], ["fs"], ["current"]),
], [
    (["!ru_text", "описание в названии"], "exp", ""),
    (["!yandex_rubric", "рубрика карточки"], "yo", ""),
    (["!overture_category"], "ov", ""),
    (["!fsq_category"], "fs", ""),
], "Табак?", "Алкогольная лицензия — косвенный довод probable; не складывается с прямыми подтверждениями.")

# ---------------------------------------------------------------- matchers map
d = new("map", 1000, "Какие матчеры вызывают какие общие блоки")
d.text(16, 22, "МАТЧЕРЫ (checks/*)", cls="g")
d.text(420, 22, "ОБЩИЕ БЛОКИ", cls="g")
d.text(760, 22, "ВНУТРИ LICMATCH", cls="g")
M = {}
for i, (k, t, h) in enumerate([("yo", "yandex: наша проверка", "sec-yandex-own"), ("pr", "providers / relink", "sec-providers"),
                               ("yc", "yandex: подрядчик", "sec-yandex-contractor"), ("fs", "foursquare", "sec-foursquare"),
                               ("ov", "overture", "sec-overture"), ("ec", "egais: catering", "sec-egais-catering"),
                               ("e4", "egais: v4", "sec-egais-v4")]):
    M[k] = d.box(16, 36 + i * 52, 210, ["!" + t], "check", href=h)
B = {}
for k, t, h, y in [("ns", "name_similarity()", "sec-names", M["yo"].y - 12), ("gen", "is_generic()", "sec-names", M["pr"].y + 8),
                   ("sim", "similar() + транслит", "sec-names", (M["pr"].y + M["yc"].y) / 2 + 30), ("exc", "одна запись — одна точка", "sec-exclusive", (M["fs"].y + M["ov"].y) / 2 + 10),
                   ("lm", "licmatch.match()", "sec-licmatch", (M["ec"].y + M["e4"].y) / 2)]:
    B[k] = d.box(420, y, 230, ["!" + t], "ref", href=h)
for k, t, h, y in [("br", "learn_brands()", "sec-brands", B["lm"].y - 34), ("na", "names_agree()", "sec-agree", B["lm"].y + 34)]:
    B[k] = d.box(760, y, 220, ["!" + t], "ref", href=h)


def link(a, b, ody=0, dy=0):
    d.arrow([(M[a].right, M[a].cy + ody), (B[b].x, B[b].cy + dy)])


link("yo", "ns"); link("yo", "gen", 6, -6); link("pr", "gen", -4, 4); link("pr", "sim", 6, -6)
link("yc", "sim"); link("fs", "sim", -6, 6); link("fs", "exc", 6, -4); link("ov", "exc", -4, 4)
link("ov", "sim", -10, 10)
link("ec", "lm", 0, -6); link("e4", "lm", -6, 6)
d.arrow([(M["e4"].right, M["e4"].cy + 6), (700, M["e4"].cy + 6), (700, B["na"].cy + 6), (B["na"].x, B["na"].cy + 6)])
d.arrow([B["lm"].r(dy=6), (710, B["lm"].cy + 6), (710, B["na"].cy - 6), (B["na"].x, B["na"].cy - 6)])
d.arrow([B["lm"].r(dy=-6), (720, B["lm"].cy - 6), (720, B["br"].cy), (B["br"].x, B["br"].cy)])

# ---------------------------------------------------------------- names
d = new("names", 860, "name_similarity и similar")
ladder(d, 16, 16, 400, [
    ("box", ["!Ключи", "выгрузка: fascia_name(до запятой), fascia_name(всё)", "справочник: fascia_name(имя)", "(fold, без ОПФ, кавычек и торговых описателей)"], "plain"),
    ("q", ["?Ключ пуст?"], ["сравнить fold() написанных имён"], "plain", "да", "нет"),
    ("box", ["!sim = max(ratio, token_sort_ratio)", "для каждого ключа выгрузки ka и ключа kb"], "plain"),
    ("q", ["?Слова kb ⊂ слов ka и len(kb) ≥ 4?", "?(справочник — часть имени выгрузки)"], ["!100"], "ok", "да", "нет"),
    ("q", ["?Слова ka ⊆ слов kb и len(ka)/len(kb) ≥ 0,5?"], ["!max(sim, token_set_ratio)"], "plain", "да", "нет"),
    ("box", ["!name_similarity = max по ключам выгрузки", "асимметрична: наша проверка Яндекса"], "lm"),
    ("q", ["?similar(): < 72 и есть латиница?"], ["повторить на translit(a), translit(b)", "«Stare Mesto» → «старе место»; max"], "plain", "да", None),
], 460, 384)

# ---------------------------------------------------------------- exclusive
d = new("exclusive", 860, "exclusive: запись, найденную несколькими объектами, получает ближайший и его дубли")
ladder(d, 16, 16, 380, [
    ("box", ["!Найденные записи поставки", "ref = id места; область — один город"], "plain"),
    ("q", ["?Одну запись нашли ≥ 2 объекта?"], ["!Без изменений"], "ok", "нет", "да"),
    ("box", ["!Победитель — объект, ближайший к записи"], "plain"),
    ("q", ["?Объект в пределах relink_m (50 м)", "?от победителя?"], ["!Остаётся strong", "дубль выгрузки: «Библио-Кофе» /", "«Библио-кофе, кофейня»"], "ok", "да", "нет"),
    ("box", ["!identity = weak", "note: «запись ближе к объекту «…» (адрес), N м отсюда»", "два Hindi на Троицком, 61 и 67"], "no"),
], 440, 404)

# ---------------------------------------------------------------- providers
d = new("providers", 900, "providers.relink: одолжить провайдера у соседней записи")
ladder(d, 16, 16, 400, [
    ("box", ["!Объект a, провайдер p ∈ {OSM, 2GIS}"], "plain"),
    ("q", ["?Флаг p у a есть?"], ["!providers: strong/current", "«запись выгрузки (url)»"], "ok", "да", "нет"),
    ("box", ["!Кандидаты b", "тот же город, флаг p, d ≤ 50 м; ближайший первым"], "plain"),
    ("q", ["?d ≤ 20 м, если a или b — общая вывеска?"], ["следующий b"], "plain", "нет", "да"),
    ("q", ["?min(similar(a,b), similar(b,a)) ≥ 72?", "?(симметрично: обе — записи выгрузки)"], ["!relink: strong/current", "«соседняя запись «b», N м, url»", "→ колонка «Связанная запись»"], "ok", "да", "нет"),
    ("box", ["!Следующий b; кончились — строки нет"], "no"),
], 460, 424)

# ---------------------------------------------------------------- yandex contractor
d = new("yandex_contractor", 900, "yandex_contractor: «Найдено» засчитывается, только если совпало имя")
ladder(d, 16, 16, 400, [
    ("box", ["!Строки объекта со статусом «Найдено»", "[имена через «;», адрес, ссылка] — из колонок", "«Яндекс: название / адрес / ссылка» выгрузки"], "plain"),
    ("q", ["?Есть хоть одна?"], ["Строки нет"], "plain", "нет", "да"),
    ("box", ["!Для каждой: max similar(имя объекта, имя)", "по именам, которые вернул Яндекс"], "ref", "sec-names"),
    ("q", ["?Хоть одна строка ≥ 72?"], ["!strong/current, «Яндекс»", "«карточка «…», адрес (проверка подрядчика)»"], "ok", "да", "нет"),
    ("box", ["!weak/current, «Яндекс (подрядчик)»", "«по адресу есть общепит, но с другим названием»"], "no"),
], 460, 424)

# ---------------------------------------------------------------- yandex own
d = new("yandex_own", 960, "yandex.judge: что ответ Яндекса говорит об этом объекте")
c = ladder(d, 16, 16, 420, [
    ("box", ["!search_url(имя, город, адрес)", "`yandex.ru/maps/<region>/search/…?ll=lon,lat&z=17"], "plain"),
    ("q", ["?Есть в кэше (ключ — search_url)?"], ["Строки нет: не запрашивался"], "plain", "нет", "да"),
    ("q", ["?Запись со сбоем (note)?"], ["!error: none/unknown", "без текста"], "no", "да", "нет"),
    ("box", ["!Кандидаты страницы", "убрать топонимы (нет адреса, имя «улица …»);", "d до объекта и name_similarity; сортировка по d"], "ref", "sec-names"),
    ("q", ["?Первый с d ≤ 150 м (60 м для общей вывески)", "?и sim ≥ 72?"], None, None, None, "нет"),
], 490, 454)
hit = c[4]
cl = d.box(490, hit.cy - 16.75, 454, ["?«Больше не работает» или «Временно не работает»?"], "q")
d.arrow([hit.r(), (cl.x, cl.cy)], "да")
p_ = d.box(490, cl.bottom + 26, 214, ["!present", "strong/current", "kind_text = рубрика"], "ok")
c_ = d.box(730, cl.bottom + 26, 214, ["!closed", "strong/closed", "довод против"], "no")
d.arrow([(p_.cx, cl.bottom), p_.t()], "нет")
d.arrow([(c_.cx, cl.bottom), c_.t()], "да")
g2 = d.box(16, p_.bottom + 26, 420, ["?Общая вывеска и ближайший кандидат ≤ 60 м?"], "q")
d.arrow([hit.b(), g2.t()], "нет", at=(hit.cx + 6, hit.bottom + 20))
am = d.box(490, g2.y - 10, 454, ["!ambiguous: weak/current", "«по адресу кто-то есть, вывеска безымянная»"], "note")
d.arrow([g2.r(), (am.x, g2.cy)], "да")
ab = d.box(16, g2.bottom + 26, 420, ["!absent: none/unknown", "«по названию и адресу не найден (рядом: …)»"], "no")
d.arrow([g2.b(), ab.t()], "нет")

# ---------------------------------------------------------------- yandex queue
d = new("yandex_queue", 960, "needs_query: вердикт объекта ещё зависит от нашего запроса?")
ladder(d, 16, 16, 420, [
    ("box", ["!Evidence без нашей проверки Яндекса", "повторно оценённый «Найдено» подрядчика — бесплатно"], "plain"),
    ("q", ["?Засчитанных групп, кроме «Яндекс», < 2?"], ["!Не нужен", "вердикт от Яндекса не зависит"], "no", "нет", "да"),
    ("q", ["?Засчитан проверкой из query.skip_checks?", "?(общепит: yandex_contractor)"], ["!Не нужен"], "no", "да", "нет"),
    ("box", ["!В очередь: needs_yandex = True", "→ yandex_queue.txt"], "ok"),
], 490, 300)

# ---------------------------------------------------------------- overture
d = new("overture", 960, "overture.run: ближайшее одноимённое место")
ladder(d, 16, 16, 420, [
    ("box", ["!Кандидаты", "d ≤ 150 м (60 м для общей вывески),", "confidence ≥ 0,5; ближайший первым"], "plain"),
    ("q", ["?similar(объект, место) ≥ 85?"], ["следующий кандидат;", "кончились — строки нет"], "plain", "нет", "да"),
    ("box", ["!Строка strong/current", "origin = datasets («meta|Overture», …),", "kind_text = category"], "plain"),
    ("box", ["!Одна запись — одна точка"], "ref", "sec-exclusive"),
    ("q", ["?Запись досталась другому объекту?"], ["!weak", "«запись ближе к объекту …»"], "no", "да", "нет"),
    ("box", ["!strong: засчитывается", "«…» (category), N м, источник, уверенность, id"], "ok"),
    ("q", ["?Правило категории: origin — только Foursquare?"], ["!Молча исключено", "засчитывает прямая проверка FSQ"], "note", "да", None),
], 490, 300)

# ---------------------------------------------------------------- foursquare
d = new("foursquare", 960, "foursquare.run: свежая открытая запись предпочтительнее ближней старой")
ladder(d, 16, 16, 420, [
    ("box", ["!Кандидаты и hits", "d ≤ 150 м (60 м для общей вывески);", "hits — similar ≥ 85, ближайший первым"], "plain"),
    ("q", ["?hits пуст?"], ["Строки нет"], "plain", "да", "нет"),
    ("box", ["!best", "первый hit, обновлённый с 2022-01-01 и без", "date_closed; если такого нет — ближайший hit"], "plain"),
    ("q", ["?best закрыт (date_closed)?"], ["!closed: strong/closed", "«закрыт с …» → после « || »"], "no", "да", "нет"),
    ("q", ["?best не обновлялся с 2022 г.?"], ["!stale: strong/stale", "«запись не обновлялась с YYYY»", "голосует о виде, не считается"], "no", "да", "нет"),
    ("box", ["!Одна запись — одна точка", "досталась соседу — weak"], "ref", "sec-exclusive"),
    ("box", ["!current: засчитывается", "«…» (категории), N м, обновлено …, id"], "ok"),
], 490, 300)

# ---------------------------------------------------------------- egais register
d = new("egais_register", 1180, "От ZIP ФСРАР к RegisterTable")
bs = [
    d.box(16, 20, 190, ["!ZIP ФСРАР", "10,9 ГБ XML,", "1,66 млн записей"], "data"),
    d.box(246, 20, 230, ["!parse()", "куски по 16 МиБ, резка по </row>;", "в XML-парсер — только куски,", "где байты содержат имя города"], "check"),
    d.box(516, 20, 140, ["!registry.tsv", "27 колонок"], "data"),
    d.box(696, 20, 230, ["!load() и срез", "русские даты, статус, вид;", "координата — только в рамке;", "заведение из текста помещения;", "только города объектов группы"], "check"),
    d.box(966, 20, 198, ["!RegisterTable", "record_id, holder_id,", "venue_name, street/house,", "lat/lon, in_force, kind"], "lm"),
]
for a_, b_ in zip(bs, bs[1:]):
    d.arrow([a_.r(), (b_.x, a_.cy)])

# ---------------------------------------------------------------- egais catering
d = new("egais_catering", 1100, "checks.egais.run_catering")
a = d.box(16, 16, 420, ["!ObjectTable поставки", "city → населённый пункт реестра;", "address → (address, address_2) у углового дома"], "plain")
lm_ = d.box(16, a.bottom + 26, 420, ["!licmatch.match(…, catering_policy)", "вид: catering (wrong = retail); действующая обязательна;", "имя обязательно: владелец или заведение; блок = город;", "точка = ИНН @ улица | дом | заведение;", "исключительность по имени, без перехода; 5 ступеней"], "ref", "sec-licmatch")
d.arrow([a.b(), lm_.t()])
outs = [
    (["!own", "strong/current — засчитывается", "«лицензия №, владелец, адрес»"], "ok"),
    (["!inactive", "strong/inactive", "«… не действует (дата)»"], "no"),
    (["!taken · other_holder ·", "!elsewhere · wrong_kind", "weak; текст по ступени"], "no"),
    (["!none", "none/unknown, без текста"], "plain"),
]
x = 480
ys = []
for i, (lines, k) in enumerate(outs):
    b = d.box(x, ys[-1].bottom + 12 if ys else 16, 300, lines, k)
    ys.append(b)
    d.arrow([(lm_.right, lm_.cy), (458, lm_.cy), (458, b.cy), (b.x, b.cy)])
d.text(800, 40, "status → оси Evidence;")
d.text(800, 56, "record_state — состояние")
d.text(800, 72, "цитируемой записи")

# ---------------------------------------------------------------- egais v4
d = new("egais_v4", 1100, "checks.egais.match_v4: какую лицензию держит запись и что это доказывает")
c = ladder(d, 16, 16, 470, [
    ("box", ["!Табак + алкоголь группы (табак первым)", "trade, risk, brand_learn = (risk == Alcohol)"], "plain"),
    ("box", ["!licmatch.match(…, v4_policy)", "вид по trade: off → retail, on → catering (или имя ≥ 0,80);", "имя — тай-брейкер; бренды learned + правило сетей;", "архивная — своя при имени ≥ 0,60; prefer: вид, действующая;", "точка = ИНН @ адрес; исключительность по сущности, с переходом"], "ref", "sec-licmatch"),
    ("box", ["!licence_status", "own → Active · inactive → Archived · taken → Taken ·", "other_chain → Other chain · wrong_kind → Other trade ·", "none → Not in registry"], "plain"),
    ("q", ["?name_agrees: лицензия своя и имя совпало?", "?manual · learned с loo_accepted · fuzzy (names_agree);", "?общая вывеска — никогда"], ["!weak", "закреплена лишь по адресу"], "no", "нет", "да"),
    ("q", ["?Слой (risk)?"], ["!Tobacco: licence_evidence", "probable — своя действующая розничная;", "weak — архивная или нерозничная;", "только текст после « || »"], "note", "табак", "алкоголь"),
    ("box", ["!Alcohol: direct, strong/current", "«лицензия …; имя: известная сеть / выученная", "пара / совпадает название» — засчитывается"], "ok"),
], 540, 340)

# ---------------------------------------------------------------- licmatch stages
d = new("licmatch", 1180, "Пять шагов licmatch.match и ключи Policy, которые читает каждый")
d.text(16, 26, "вход: ObjectTable × RegisterTable + Policy")
st = []
specs = [
    ("1 · prepare", ["канонические стороны:", "адрес → Toponym,", "имя → ключ вывески,", "владельца, заведения;", "ключ точки"], "home_cities, unit", "sec-lm-address"),
    ("2 · score", ["0,5·адрес + 0,2·имя", "+ 0,3·расстояние", "по всем парам;", "выученные бренды"], "params, brands", "sec-lm-score"),
    ("3 · admit", ["именованные условия —", "булевы матрицы;", "допуск = AND", "условий политики"], "kinds, name_policy", "sec-lm-admit"),
    ("4 · assign", ["ранжирование с prefer;", "жадная раздача точек,", "сильнейшая пара", "первой"], "prefer, exclusive", "sec-lm-assign"),
    ("5 · explain", ["статус; для", "неназначенных —", "лестница ступеней", "и failed_rule"], "explain", "sec-lm-explain"),
]
for i, (t, lines, keys, h) in enumerate(specs):
    st.append(d.box(16 + i * 236, 40, 196, ["!" + t] + lines + ["", "`" + keys], "lm", href=h, h=170, valign="top"))
for (a_, b_), lab in zip(zip(st, st[1:]), ["Sides", "Scored", "маски", "Selection"]):
    d.arrow([a_.r(), (b_.x, a_.cy)], lab)
mrb = d.box(16, 250, 1148, ["!MatchResult · одна строка на объект", "`status: own | inactive | taken | other_holder | other_chain | wrong_kind | elsewhere | none", "`record_id · failed_rule · score, addr, name, distance · name_basis · unit · claimed_by · margin"], "data")
d.arrow([st[-1].b(), (st[-1].cx, mrb.y)])

# ---------------------------------------------------------------- lm score
d = new("lm_score", 1100, "Оценка пары объект × запись реестра")
ad = d.box(16, 16, 520, ["!address = 0,6·street + 0,4·house", "street = 0,6·token_sort + 0,4·token_set; конфликт типов улиц ×0,6", "house: тот же номер и корпус 1,0 · корпус с одной стороны 0,85 ·", "корпуса разные 0,6 · номера разные 0,0 · нет с обеих 0,5 · с одной 0,3", "конфликт населённых пунктов ×0,4, односторонний ×0,8"], "plain")
nm = d.box(566, 16, 518, ["!name — лучшее из name_fields (владелец / заведение)", "fuzzy: WRatio строк", "alias: известная или выученная пара → 1,0", "generic: общая вывеска — член не оценивается,", "его вес раздаётся остальным"], "plain")
q = d.box(330, ad.bottom + 30, 440, ["?У обеих сторон координата и d ≤ max_m (100 м)?"], "q")
d.arrow([ad.b(), (ad.cx, q.y - 14), (q.cx - 40, q.y - 14), (q.cx - 40, q.y)])
d.arrow([nm.b(), (nm.cx, q.y - 14), (q.cx + 40, q.y - 14), (q.cx + 40, q.y)], head=False)
geo = d.box(16, q.bottom + 40, 500, ["!Геомаршрут", "score = 0,5·address + 0,2·name + 0,3·(1 − d/100)", "общая вывеска: (0,5·address + 0,3·dist) / 0,8"], "lm")
q2 = d.box(566, q.bottom + 40, 300, ["?У записи нет координаты?"], "q")
d.arrow([(q.x + 60, q.bottom), (q.x + 60, geo.y)], "да")
d.arrow([(q.right - 60, q.bottom), (q.right - 60, q2.y)], "нет")
aro = d.box(566, q2.bottom + 30, 518, ["!Адресный маршрут: street ≥ 0,90 и house ≥ 0,85", "score = (0,5·address + 0,2·name) / 0,7", "общая вывеска: score = address"], "lm")
d.arrow([(q2.cx - 100, q2.bottom), (q2.cx - 100, aro.y)], "да")
nan = d.box(900, q2.y - 6, 184, ["!score = NaN", "«не рассматривалось»"], "no")
d.arrow([q2.r(), (nan.x, q2.cy)], "нет")
acc = d.box(16, aro.bottom + 30, 1068, ["!Условие address: score ≥ 0,45 И address ≥ 0,72", "0,72 = «та же улица, номер дома написан с одной стороны»; разные номера дают 0,60 — соседнее здание отсечено"], "ok")
d.arrow([geo.b(), (geo.cx, acc.y)])
d.arrow([aro.b(), (aro.cx, acc.y)])

# ---------------------------------------------------------------- lm assign
d = new("lm_assign", 1000, "Жадная раздача точек")
c = ladder(d, 16, 16, 460, [
    ("box", ["!ranked", "score пар, прошедших address;", "+ PREFER_LIFT·2^k за каждое условие prefer", "(v4: вид подходит > действует > сама оценка)"], "plain"),
    ("box", ["!Допущенные пары по убыванию ranked", "ничьи — меньший номер объекта, затем записи"], "plain"),
    ("q", ["?Объект уже получил точку?"], ["пропустить пару"], "plain", "да", "нет"),
    ("q", ["?Точку (unit) держит другой объект, и он не «тот же»?", "?catering: то же или согласное имя; v4: та же сущность"], None, None, None, "нет"),
    ("box", ["!Объект получает запись", "или делит точку с «тем же»"], "ok"),
], 530, 440)
fb = d.box(530, c[3].cy - 26, 440, ["?fallback? v4 — да: к следующей свободной точке;", "?catering — нет"], "q")
d.arrow([c[3].r(), (fb.x, c[3].cy)], "да")
tk = d.box(530, fb.bottom + 40, 440, ["!taken", "осталась без точки при допущенных парах;", "claimed_by, margin"], "no")
d.arrow([(fb.cx, fb.bottom), (fb.cx, tk.y)], "нет")
d.text(fb.right - 4, fb.y - 6, "да → следующая пара", "end")
d.text(16, c[-1].bottom + 22, "Сущности v4: ≤ 50 м и вывеска token_set ≥ 80 (не общая), или то же написанное имя ≤ 5 м.")

# ---------------------------------------------------------------- lm explain
d = new("lm_explain", 1080, "Лестницы объяснений двух режимов")
h = d.box(16, 16, 300, ["?Назначен?"], "q")
ow = d.box(366, 16, 300, ["!own / inactive", "по in_force своей записи"], "ok")
d.arrow([h.r(), (ow.x, h.cy)], "да")
t = d.box(16, h.bottom + 26, 300, ["?Проиграл распределение?"], "q")
tk = d.box(366, t.y - 8, 300, ["!taken", "failed_rule = exclusive"], "no")
d.arrow([h.b(), t.t()], "нет")
d.arrow([t.r(), (tk.x, t.cy)], "да")
d.arrow([t.b(), (t.cx, t.bottom + 20), (200, t.bottom + 20), (200, t.bottom + 50)], "нет: catering", at=(206, t.bottom + 40))
d.arrow([(t.cx, t.bottom + 20), (760, t.bottom + 20), (760, t.bottom + 50)], "нет: v4", at=(766, t.bottom + 40))
cat = ladder(d, 40, t.bottom + 52, 360, [
    ("q", ["?address ∧ kind ∧ in_force"], ["!other_holder"], "no", "", "пусто"),
    ("q", ["?address ∧ kind ∧ name"], ["!inactive"], "no", "", "пусто"),
    ("q", ["?near ∧ kind ∧ in_force ∧ name, не address"], ["!elsewhere"], "no", "", "пусто"),
    ("q", ["?address ∧ kind"], ["!other_holder"], "no", "", "пусто"),
    ("q", ["?address ∧ wrong_kind ∧ in_force"], ["!wrong_kind"], "no", "", "пусто"),
    ("box", ["!none"], "plain"),
], 430, 150, gap=18)
v4 = ladder(d, 640, t.bottom + 52, 240, [
    ("q", ["?address ∧ kind"], ["!other_chain"], "no", "", "пусто"),
    ("q", ["?address"], ["!wrong_kind"], "no", "", "пусто"),
    ("box", ["!none"], "plain"),
], 910, 154, gap=18)
d.text(16, cat[-1].bottom + 24, "Цитируется лучший по оценке кандидат первой непустой ступени; failed_rule — условия допуска, которые он не прошёл.")

# ---------------------------------------------------------------- brands
d = new("brands", 1000, "learn_brands: голосование по адресам с конкуренцией за точку")
c = ladder(d, 16, 16, 470, [
    ("box", ["!strength", "пары оценены БЕЗ члена имени (адрес + расстояние);", "только допущенные по address и виду; слой алкоголя"], "plain"),
    ("box", ["!Раунд 0: затравка — присутствие", "каждый допущенный лицензиат на каждом адресе"], "plain"),
    ("box", ["!Раздать точки по одной на объект", "value = strength + 0,15·prior"], "plain"),
    ("box", ["!Голос = точка, которую объект получил", "счёт по различным адресам вывески"], "plain"),
    ("box", ["!prior = log((c + s·b)/(n + s)) − log(b)", "b = 0,05, s = 1; без собственного адреса (leave-one-site-out)"], "plain"),
    ("q", ["?Назначение изменилось?", "?(цикл из 2 тактов → колеблющиеся не голосуют)"], None, None, None, "нет"),
    ("box", ["!Пара принята", "sites ≥ 3, lift ≥ 3 и share ≥ 0,3;", "или франшиза: share ≥ 0,1 и holder_cover ≥ 0,5"], "plain"),
    ("box", ["!loo_accepted", "принята, даже если убрать любой голосовавший адрес?"], "plain"),
    ("box", ["!Алиас: name = 1,0", "кроме адресов, где пара голосовала за себя", "(не loo_accepted) — там обычное сравнение строк"], "lm"),
], 0, 0)
d.arrow([c[5].r(), (520, c[5].cy), (520, c[2].cy), (c[2].right, c[2].cy)], "да: следующий раунд", at=(526, (c[2].cy + c[5].cy) / 2))
d.text(560, c[6].cy - 4, "lift = cover / base_rate: федеральный лицензиат,")
d.text(560, c[6].cy + 12, "стоящий почти везде, получает lift ≈ 1")

# ---------------------------------------------------------------- agree
d = new("agree", 960, "names_agree: имена согласны?")
ladder(d, 16, 16, 470, [
    ("q", ["?Ключ общий (только слова вида заведения, VENUE_WORDS)?"], ["!нет"], "no", "да", "нет"),
    ("q", ["?На ключах и их латинской транслитерации («El Fuego» /", "?«Эль Фуэго»): ratio ≥ 85, или все слова одного ключа", "?входят в другой, или общее отличительное слово ≥ 5 букв?"], ["!нет"], "no", "нет", "да"),
    ("q", ["?Совпадение держится только на словах-топонимах", "?региона (place_words: «двина», «север», …)?"], ["!нет"], "no", "да", "нет"),
    ("box", ["!да"], "ok"),
], 540, 160)

# ---------------------------------------------------------------- kind ladders
d = new("kind", 1180, "classify и fold_votes: лестницы трёх категорий")
cols = [
    ("ОБЩЕПИТ", [("автомат|кофемат|вендинг|кофемашин", "автомат", "note"),
                 ("оптом|оптов|оборудовани|кафедр…", "нет", "no"),
                 ("кафе(?!др)|кофе|ресторан|столов|бар…", "да", "ok"),
                 ("магазин|маркет|компани|салон…", "нет", "no")]),
    ("АЛКОГОЛЬ", [("аптек|медицин|клиник|детск|школ|храм…", "no", "no"),
                  ("алкогол|вино|пиво|разливн|бар|паб…", "да", "ok"),
                  ("одежд|обув|ремонт|салон|банк|почт…", "no", "no")]),
    ("ТАБАК", [("аптек|медицин|клиник|детск|школ|храм…", "no", "no"),
               ("табак|сигар|вейп|кальян|никотин…", "yes", "ok"),
               ("одежд|обув|…|ресторан|столов", "no", "no")]),
]
bottoms = []
for i, (title, steps) in enumerate(cols):
    x = 16 + i * 388
    d.text(x, 22, title + ": RU_TEXT И YANDEX_RUBRIC", cls="g")
    items = [("q", ["?" + rx], ["!" + {"yes": "да", "no": "нет"}.get(role, role)], kind, "", "нет") for rx, role, kind in steps]
    items.append(("box", ["промолчал → ''"], "plain"))
    bs = ladder(d, x, 34, 268, items, x + 296, 80, gap=16)
    bottoms.append(bs[-1].bottom)
yb = max(bottoms) + 24
en = d.box(16, yb, 560, ["!Английские схемы", "fsq_category: «|»-список; yes — любая метка ~ any_yes;", "no — у всех меток верхний уровень ∈ all_no_top (Retail, …)", "overture_category: своя лестница (restaurant|cafe|… → yes; store|shop|… → no)", "egais_activity (алкоголь): любая непустая → yes"], "plain")
fo = d.box(616, yb, 548, ["!fold_votes по высказавшимся", "никто → нет данных · один вид → он", "yes и no → противоречие", "vending + один другой → другой"], "cat")

# ---------------------------------------------------------------- write
out = Path(__file__).resolve().parent
for name, dg in D.items():
    (out / f"{name}.md").write_text(
        "<!-- generated by docs/_workflow/diagrams.py; edit that, not this -->\n"
        "```{=html}\n<div class=\"arch\">\n" + dg.svg() + "\n</div>\n```\n", encoding="utf-8")
if ERRORS:
    sys.exit("\n".join(ERRORS))
