import os
import datetime
import json
import requests
from woocommerce import API
from collections import defaultdict, Counter

WOO_URL = os.environ.get("WOO_URL", "").rstrip("/")
WOO_CLIENT = os.environ.get("WOO_CLIENT")
WOO_SECRET = os.environ.get("WOO_SECRET")
RECS_API_TOKEN = os.environ.get("RECS_API_TOKEN")

wcapi = API(
    url=WOO_URL,
    consumer_key=WOO_CLIENT,
    consumer_secret=WOO_SECRET,
    version="wc/v3",
    timeout=60
)

KNOWN_BRANDS = ["tokaido", "adidas", "arawaza", "venum", "kamikaze", "kaze", "hayashi", "shureido", "budo-nord"]
CACHE_FILE = "orders_cache.json"

def fetch_products_catalog():
    products_meta = {}
    page = 1
    print("Chargement des métadonnées du catalogue produits...")
    while True:
        try:
            response = wcapi.get("products", params={"per_page": 100, "page": page, "status": "publish"})
            if response.status_code != 200:
                break
            data = response.json()
            if not data or not isinstance(data, list):
                break
            for p in data:
                sku = p.get("sku")
                if sku:
                    categories = [c["name"].lower() for c in p.get("categories", [])]
                    products_meta[sku] = {
                        "id": p.get("id"),
                        "name": p.get("name", "").lower(),
                        "categories": categories,
                        "sku": sku,
                        "price": float(p.get("price") or 0.0),
                        "stock_status": p.get("stock_status", "instock")
                    }
            if len(data) < 100:
                break
            page += 1
        except Exception as e:
            print(f"Erreur catalogue : {e}")
            break
    print(f"Métadonnées chargées pour {len(products_meta)} produits.")
    return products_meta

def fetch_incremental_orders():
    """Charge le cache existant et récupère uniquement les commandes manquantes."""
    existing_orders = []
    after_date = "2018-01-01T00:00:00"

    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                existing_orders = json.load(f)
                if existing_orders:
                    dates = [o.get("date_created") for o in existing_orders if o.get("date_created")]
                    if dates:
                        after_date = max(dates)
                        print(f"Cache trouvé. Dernier historique enregistré : {after_date}")
        except Exception as e:
            print(f"Erreur lecture cache : {e}")

    print(f"Récupération des nouvelles commandes depuis : {after_date}...")
    new_orders = []
    page = 1
    
    while True:
        try:
            params = {
                "per_page": 100,
                "page": page,
                "status": "completed",
                "after": after_date
            }
            response = wcapi.get("orders", params=params)
            if response.status_code != 200:
                break
            data = response.json()
            if not data or not isinstance(data, list):
                break
            new_orders.extend(data)
            if len(data) < 100:
                break
            page += 1
        except Exception as e:
            print(f"Erreur récupération commandes : {e}")
            break

    existing_ids = {o["id"] for o in existing_orders}
    truly_new = [o for o in new_orders if o["id"] not in existing_ids]
    
    all_orders = existing_orders + truly_new
    print(f"Ajout de {len(truly_new)} nouvelles commandes. Total en mémoire : {len(all_orders)}")

    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(all_orders, f, ensure_ascii=False, indent=2)

    return all_orders

def check_category_compatibility(main_meta, rel_meta):
    if rel_meta.get("stock_status") != "instock":
        return False
    m_cats = set(main_meta.get("categories", []))
    r_cats = set(rel_meta.get("categories", []))
    m_name = main_meta.get("name", "")
    r_name = rel_meta.get("name", "")

    strict_disciplines = {"kobudo", "yoseikan", "yoseikan budo", "nanbudo"}
    if m_cats.intersection(strict_disciplines) != r_cats.intersection(strict_disciplines):
        return False

    is_m_kata = any("kata" in c for c in m_cats) or "kata" in m_name
    is_r_protection = any(c in r_cats for c in ["protections", "gants", "protège-tibias", "casques", "plastrons"])
    if is_m_kata and is_r_protection:
        return False

    is_m_kid = any(kw in m_name or any(kw in c for c in m_cats) for kw in ["enfant", "junior", "kid"])
    is_r_kid = any(kw in r_name or any(kw in c for c in r_cats) for kw in ["enfant", "junior", "kid"])
    neutral_categories = {"sacs", "bagagerie", "accessoires", "matériel club", "soin", "ceintures"}
    if is_m_kid != is_r_kid and not r_cats.intersection(neutral_categories):
        return False

    is_m_fem = "femme" in m_name or "féminin" in m_name or "fille" in m_name
    is_m_hom = "homme" in m_name or "masculin" in m_name or "garçon" in m_name
    is_r_fem = "femme" in r_name or "féminin" in r_name or "fille" in r_name
    is_r_hom = "homme" in r_name or "masculin" in r_name or "garçon" in r_name
    if (is_m_fem and is_r_hom) or (is_m_hom and is_r_fem):
        return False

    return True

def calculate_recommendations(orders, products_meta):
    pairs = defaultdict(Counter)
    global_sales = Counter()
    
    for order in orders:
        skus = [item['sku'] for item in order.get('line_items', []) if item.get('sku')]
        for sku in skus:
            global_sales[sku] += 1
        for i in range(len(skus)):
            for j in range(len(skus)):
                if i != j:
                    pairs[skus[i]][skus[j]] += 1

    best_sellers_list = [sku for sku, count in global_sales.most_common() if sku in products_meta]
    if not best_sellers_list:
        best_sellers_list = list(products_meta.keys())

    recommendations = {}

    for main_sku, main_meta in products_meta.items():
        valid_recs = []
        related_counts = pairs[main_sku]

        # 1. Historique d'achats communs
        for rel_sku, count in related_counts.most_common(15):
            if rel_sku not in products_meta or rel_sku == main_sku:
                continue
            if count >= 2 or check_category_compatibility(main_meta, products_meta[rel_sku]):
                if rel_sku not in valid_recs:
                    valid_recs.append(rel_sku)
            if len(valid_recs) == 4:
                break

        # 2. Fallback Catégories / Marques / Prix
        if len(valid_recs) < 4:
            main_cats = set(main_meta.get("categories", []))
            main_price = main_meta.get("price", 0.0)
            fallback_candidates = []

            for fallback_sku, fallback_meta in products_meta.items():
                if fallback_sku == main_sku or fallback_sku in valid_recs:
                    continue
                fallback_cats = set(fallback_meta.get("categories", []))
                if main_cats.intersection(fallback_cats) and check_category_compatibility(main_meta, fallback_meta):
                    score = 0
                    main_brand = next((b for b in KNOWN_BRANDS if b in main_meta["name"]), None)
                    fallback_brand = next((b for b in KNOWN_BRANDS if b in fallback_meta["name"]), None)
                    if main_brand and main_brand == fallback_brand:
                        score += 50
                    fb_price = fallback_meta.get("price", 0.0)
                    if main_price > 0:
                        ratio = fb_price / main_price
                        if 0.8 <= ratio <= 1.3:
                            score += 20
                    fallback_candidates.append((score, fallback_sku))

            fallback_candidates.sort(key=lambda x: x[0], reverse=True)
            for _, f_sku in fallback_candidates:
                if f_sku not in valid_recs:
                    valid_recs.append(f_sku)
                if len(valid_recs) == 4:
                    break

        # 3. Remplissage ultime par les Best-Sellers
        if len(valid_recs) < 4:
            for b_sku in best_sellers_list:
                if b_sku != main_sku and b_sku not in valid_recs and products_meta[b_sku].get("stock_status") == "instock":
                    valid_recs.append(b_sku)
                if len(valid_recs) == 4:
                    break

        recommendations[main_sku] = valid_recs
        print(f"[PRODUIT] {main_sku} -> 4 recs : {valid_recs}")

    return recommendations

def push_to_wordpress_in_batches(recs, batch_size=50):
    """Envoie les recommandations par paquets pour éviter les erreurs de timeout (504)."""
    if not recs:
        return
        
    url = f"{WOO_URL}/wp-json/custom/v1/update-recommendations"
    headers = {
        "Content-Type": "application/json",
        "X-Recommendation-Token": RECS_API_TOKEN
    }
    
    items = list(recs.items())
    total_items = len(items)
    
    for i in range(0, total_items, batch_size):
        batch = dict(items[i:i + batch_size])
        print(f"Envoi d'un lot de {len(batch)} produits (du {i+1} au {min(i+batch_size, total_items)} sur {total_items})...")
        
        try:
            response = requests.post(url, json=batch, headers=headers, timeout=60)
            response.raise_for_status()
            print(f"Lot validé par WordPress : {response.text}")
        except requests.exceptions.RequestException as e:
            print(f"Erreur sur ce lot : {e}")

if __name__ == "__main__":
    products_meta = fetch_products_catalog()
    orders = fetch_incremental_orders()
    recs = calculate_recommendations(orders, products_meta)
    push_to_wordpress_in_batches(recs)
