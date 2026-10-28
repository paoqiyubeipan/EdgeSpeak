"""生成 uk_english.json（英文卡片以英式英语为准用的数据）：python build_uk_english.py

- spelling：美式拼写 → 英式拼写。来源是 tysto.com 的英美拼写对照表，经 hyperreality/American-British-English-Translator
  整理（MIT，Copyright (c) 2016）。那份表里混着不少古旧或只在部分义项成立的写法（waggon、gaol、mediaeval、
  reflexion、philtre……），现代英式并不这么写，所以只保留符合常规英美差异规律的对照（-our、-re、-ise、-yse、
  双写 l、-ogue、-ence），再加上人工核对过的一批常用词。
- partial：只对部分义项或词性成立的拼写差异，只加说明，不改卡片标题。
- vocab：常见的英美用词差异。
"""
import json
import os
import re
import urllib.request

SOURCE = ("https://raw.githubusercontent.com/hyperreality/American-British-English-Translator/"
          "16ec8158bd31fd8fc13bf29a2a5165ca12c7dd3f/data/american_spellings.json")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uk_english.json")

# 常规规律之外、人工核对过的英式写法（词根；以这些开头的美式词都保留：anemia、anemic……）
ALLOWED_ROOTS = (
    "gray", "plow", "mold", "smolder", "molt", "aluminum", "airplane", "pajama", "mustache", "cozy", "cozie",
    "jewelry", "artifact", "skeptic", "sulfur", "sulfate", "sulfide", "ax", "aging", "judgment", "acknowledgment",
    "maneuver", "anemi", "anesthe", "hemo", "hemato", "hemorrh", "leukemi", "esophag", "estrogen", "pediatr",
    "orthoped", "fetus", "fetuses", "fetal", "cesarean", "diarrhe", "gynecolog", "archeolog", "paleo", "ameba",
    "catalog", "dialog", "prolog", "epilog", "monolog", "travelog", "demagog", "pedagog", "analyz", "paralyz",
    "catalyz", "dialyz", "defense", "offense", "pretense", "jeweler", "mom", "check", "pajamas", "cozily",
    "donut", "whiskey", "story", "tire", "curb", "draft", "program", "practic", "licens", "meter", "disk",
    "aesthetic", "manoeuv", "cheque", "tidbit", "furor", "mollusk", "sulfurous", "vise", "plowed", "plows",
    "inquir", "install", "enroll", "fulfill", "instill", "distill", "appall", "enthrall", "skillful", "willful",
    "yogurt", "center", "cozi", "clarinetist", "eon", "esthet", "fecal", "feces", "gonorrhe", "licorice", "likable",
    "louver", "omelet", "outmaneuver", "paresthe", "pedophil", "pederast", "pharmacope", "phony", "pouf",
    "reconnoiter", "remold", "snowplow", "steatorrhe", "synesthe", "thalassemi", "toxemi", "dyspne", "cesium",
    "worship", "balk", "behoove", "battleax", "binging", "epaulet", "flutist", "videodisk", "salesroom", "etiolog",
    "mustachio", "leukemogen",
)
# 规律对得上但不对的：英式也写 license（动词）；ton 和 tonne 是不同的单位；gage 另有“抵押”的意思；
# annex 只有名词写 annexe；micrometer 指测微计时不变；analog 在电子、计算机里英式也常写 analog；glamour 美式也这么写
WRONG = {"licensed", "licensing", "ton", "tons", "gage", "gaged", "gages", "annex", "micrometer", "micrometers",
         "analog", "analogs", "glamor", "glamorize", "glamorized", "glamorizes", "glamorizing", "filet", "filets",
         "fileted", "fileting", "busses", "bussing", "minibusses", "pummel", "edema"}
# 表里写错或缺的，直接给出正确的对照
FIXED = {"pummeled": "pummelled", "pummeling": "pummelling", "edema": "oedema", "yogurt": "yoghurt",
         "yogurts": "yoghurts"}


def regular(us, uk):
    """美式 → 英式的差异是否属于常规规律。"""
    patterns = [
        (r"or", r"our"),  # color → colour
        (r"er", r"re"),  # center → centre
        (r"iz", r"is"), (r"yz", r"ys"),  # organize → organise、analyze → analyse
        (r"l", r"ll"), (r"ll", r"l"),  # traveler → traveller、fulfill → fulfil
        (r"og", r"ogue"), (r"ense", r"ence"),
    ]
    for a, b in patterns:
        for m in re.finditer(a, us):
            if us[:m.start()] + b + us[m.end():] == uk:
                return True
    # 一个词里有两处常规差异（colorization → colourisation）
    for a, b in patterns:
        for m in re.finditer(a, us):
            once = us[:m.start()] + b + us[m.end():]
            if once != us and any(once[:n.start()] + d + once[n.end():] == uk
                                  for c, d in patterns for n in re.finditer(c, once)):
                return True
    return False


# 只对部分义项成立的：不改标题，只加一句说明
PARTIAL = {
    "meter": "指长度单位时英式写作 metre（仪表仍写 meter）",
    "meters": "指长度单位时英式写作 metres（仪表仍写 meters）",
    "micrometer": "指长度单位（微米）时英式写作 micrometre（测微计仍写 micrometer）",
    "tire": "指轮胎时英式写作 tyre（“疲倦”仍写 tire）",
    "tires": "指轮胎时英式写作 tyres（“疲倦”仍写 tires）",
    "program": "指节目、计划时英式写作 programme（计算机程序仍写 program）",
    "programs": "指节目、计划时英式写作 programmes（计算机程序仍写 programs）",
    "practice": "作动词时英式写作 practise（名词仍写 practice）",
    "practices": "作动词时英式写作 practises（名词仍写 practices）",
    "license": "作名词时英式写作 licence（动词仍写 license）",
    "licenses": "作名词时英式写作 licences（动词仍写 licenses）",
    "draft": "指气流、生啤、吃水时英式写作 draught（草稿、汇票仍写 draft）",
    "disk": "英式一般写作 disc（计算机磁盘仍常写 disk）",
    "disks": "英式一般写作 discs（计算机磁盘仍常写 disks）",
    "dialog": "英式写作 dialogue（软件里的对话框常写 dialog）",
    "dialogs": "英式写作 dialogues（软件里的对话框常写 dialogs）",
    "analog": "英式写作 analogue（电子、计算机里也常写 analog）",
    "annex": "作名词（附属建筑）时英式写作 annexe（动词仍写 annex）",
    "check": "指支票时英式写作 cheque；指账单时英式说 bill",
    "checks": "指支票时英式写作 cheques",
    "curb": "指路缘时英式写作 kerb",
    "story": "指楼层时英式写作 storey",
    "stories": "指楼层时英式写作 storeys",
    "fall": "指秋天时英式说 autumn",
    "groin": "指防波堤时英式写作 groyne（腹股沟仍写 groin）",
    "checker": "指棋盘方格时英式写作 chequer（检查者仍写 checker）",
    "checkered": "英式写作 chequered（有方格图案的；盛衰无常的）",
}

# 常见的英美用词差异（美式 → 英式）
VOCAB = {
    "apartment": "英式常说 flat", "apartments": "英式常说 flats",
    "elevator": "英式说 lift", "elevators": "英式说 lifts",
    "truck": "英式常说 lorry", "trucks": "英式常说 lorries",
    "cookie": "指饼干时英式说 biscuit（网页的 cookie 英式也叫 cookie）",
    "cookies": "指饼干时英式说 biscuits（网页的 cookies 英式也叫 cookies）",
    "candy": "英式说 sweets",
    "gasoline": "英式说 petrol", "gas": "指汽油时英式说 petrol",
    "sidewalk": "英式说 pavement", "sidewalks": "英式说 pavements",
    "subway": "指地铁时英式说 the Underground / the Tube",
    "vacation": "英式说 holiday", "vacations": "英式说 holidays",
    "trash": "英式说 rubbish", "garbage": "英式说 rubbish",
    "faucet": "英式说 tap", "faucets": "英式说 taps",
    "diaper": "英式说 nappy", "diapers": "英式说 nappies",
    "flashlight": "英式说 torch", "flashlights": "英式说 torches",
    "eraser": "英式常说 rubber", "erasers": "英式常说 rubbers",
    "pants": "英式说 trousers（英式的 pants 指内裤）",
    "soccer": "英式说 football",
    "mailman": "英式说 postman", "mailbox": "英式说 postbox",
    "cellphone": "英式说 mobile (phone)", "cellphones": "英式说 mobiles",
    "highway": "指高速公路时英式说 motorway", "freeway": "英式说 motorway",
    "movie": "英式常说 film", "movies": "英式常说 films（去看电影：go to the cinema）",
    "closet": "英式常说 wardrobe（衣柜）或 cupboard",
    "drugstore": "英式说 chemist's", "pharmacy": "英式也常说 chemist's",
    "stove": "英式常说 cooker",
    "windshield": "英式说 windscreen",
    "gotten": "英式一般用 got（have got）",
    "math": "英式说 maths",
    "restroom": "英式说 toilet", "restrooms": "英式说 toilets",
    "sweater": "英式常说 jumper", "sweaters": "英式常说 jumpers",
    "vest": "英式说 waistcoat（英式的 vest 指背心、汗衫）",
    "store": "指商店时英式常说 shop", "stores": "指商店时英式常说 shops",
    "mom": "英式说 mum", "moms": "英式说 mums", "mommy": "英式说 mummy",
    "sneakers": "英式说 trainers",
    "semester": "英式常说 term",
    "crib": "指婴儿床时英式说 cot",
    "stroller": "英式说 pushchair / buggy",
    "ladybug": "英式说 ladybird",
    "zucchini": "英式说 courgette",
    "eggplant": "英式说 aubergine",
    "cilantro": "英式说 coriander",
    "chips": "英式说 crisps（英式的 chips 指薯条）",
    "fries": "英式说 chips",
    "period": "指句号时英式说 full stop",
    "parentheses": "英式常说 brackets",
    "resume": "指简历时英式说 CV",
    "attorney": "英式说 solicitor / barrister（律师）",
    "line": "指排队的队伍时英式说 queue",
    "realtor": "英式说 estate agent",
    "downtown": "英式常说 city centre / town centre",
    "zip": "指邮编时英式说 postcode",
    "trunk": "指汽车后备箱时英式说 boot",
    "hood": "指汽车引擎盖时英式说 bonnet",
}


def main():
    with urllib.request.urlopen(SOURCE, timeout=60) as r:
        src = json.load(r)
    spelling, dropped = {}, []
    for us, uk in src.items():
        us, uk = us.lower(), uk.lower()
        if us in WRONG or us in PARTIAL or us == uk:
            continue
        if regular(us, uk) or us.startswith(ALLOWED_ROOTS):
            spelling[us] = uk
        else:
            dropped.append(f"{us}→{uk}")
    for us in WRONG:
        spelling.pop(us, None)
    spelling.update(FIXED)
    out = {
        "_source": "spelling: tysto.com UK/US spelling list as curated in hyperreality/American-British-English-Translator "
                   "(MIT, Copyright (c) 2016), filtered and corrected by build_uk_english.py; partial and vocab: EdgeSpeak",
        "spelling": dict(sorted(spelling.items())),
        "partial": PARTIAL,
        "vocab": VOCAB,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=0)
    print(f"保留 {len(spelling)} 对，去掉 {len(dropped)} 对：{'、'.join(dropped)}")


if __name__ == "__main__":
    main()
