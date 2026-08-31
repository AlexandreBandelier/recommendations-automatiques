import os
import datetime
import json
import requests
from woocommerce import API
from collections import defaultdict, Counter

# Liste de tes différents sites WooCommerce
SITES = [
    {
        "id": "site_1",
        "url": os.environ.get("WOO_URL_1", "").rstrip("/"),
        "client": os.environ.get("WOO_CLIENT_1"),
        "secret": os.environ.get("WOO_SECRET_1"),
        "token": os.environ.get("RECS_API_TOKEN_1")
    },
    # {
    #     "id": "site_2",
    #     "url": os.environ.get("WOO_URL_2", "").rstrip("/"),
    #     "client": os.environ.get("WOO_CLIENT_2"),
    #     "secret": os.environ.get("WOO_SECRET_2"),
    #     "token": os.environ.get("RECS_API_TOKEN_2")
    # }
]

KNOWN_BRANDS = ["tokaido", "adidas", "arawaza", "venum", "kamikaze", "kaze", "hayashi", "shureido", "budo-nord"]

def fetch_products_catalog(wcapi):
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
                        "categories_set": set(categories), # OPTIMISATION 2 : set pré-calculé pour accélérer les tests
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

def fetch_incremental_orders(wcapi, site_id):
    """Charge le cache, récupère les commandes manquantes et nettoie les doublons (OPTIMISATION 1)."""
    cache_file = f"orders_cache_{site_id}.json"
    existing_orders = []
    after_date = "2018-01-01T00:00:00"

    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                existing_orders = json.load(f)
                if existing_orders:
                    dates = [o.get("date_created") for o in existing_orders if o.get("date_created")]
                    if dates:
                        after_date = max(dates)
                        print(f"Cache trouvé pour {site_id}. Dernier historique : {after_date}")
        except Exception as e:
            print(f"Erreur lecture cache {site_id} : {e}")

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

    # OPTIMISATION 1 : Utilisation stricte d'un dictionnaire indexé par ID pour purger tout doublon potentiel
    orders_dict = {o["id"]: o for o in existing_orders}
    new_count = 0
    for o in new_orders:
        if o["id"] not in orders_dict:
            orders_dict[o["id"]] = o
            new_count += 1

    all_orders = list(orders_dict.values())
    print(f"Ajout de {new_count} nouvelles commandes. Total consolidé : {len(all_orders)}")

    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(all_orders, f, ensure_ascii=False, indent=2)

    return all_orders

def check_category_compatibility(main_meta, rel_meta):
    if rel_meta.get("stock_status") != "instock":
        return False
    
    # OPTIMISATION 2 : Utilisation des sets pré-calculés
    m_cats = main_meta["categories_set"]
    r_cats = rel_meta["categories_set"]
    m_name = main_meta["name"]
    r_name = rel_meta["name"]

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
            main_cats = main_meta["categories_set"]
            main_price = main_meta["price"]
            fallback_candidates = []

            for fallback_sku, fallback_meta in products_meta.items():
                if fallback_sku == main_sku or fallback_sku in valid_recs:
                    continue
                fallback_cats = fallback_meta["categories_set"]
                if main_cats.intersection(fallback_cats) and check_category_compatibility(main_meta, fallback_meta):
                    score = 0
                    main_brand = next((b for b in KNOWN_BRANDS if b in main_meta["name"]), None)
                    fallback_brand = next((b for b in KNOWN_BRANDS if b in fallback_meta["name"]), None)
                    if main_brand and main_brand == fallback_brand:
                        score += 50
                    fb_price = fallback_meta["price"]
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
        
        # OPTIMISATION 3 : Traçabilité détaillée pour vérification rapide
        print(f"[PRODUIT] {main_sku} -> 4 recs : {valid_recs}")

    return recommendations

def push_to_wordpress_in_batches(recs, site_url, token, batch_size=50):
    if not recs:
        return
        
    url = f"{site_url}/wp-json/custom/v1/update-recommendations"
    headers = {
        "Content-Type": "application/json",
        "X-Recommendation-Token": token
    }
    
    items = list(recs.items())
    total_items = len(items)
    
    for i in range(0, total_items, batch_size):
        batch = dict(items[i:i + batch_size])
        print(f"Envoi d'un lot de {len(batch)} produits vers {site_url}...")
        
        try:
            response = requests.post(url, json=batch, headers=headers, timeout=60)
            response.raise_for_status()
            print(f"Lot validé : {response.text}")
        except requests.exceptions.RequestException as e:
            print(f"Erreur sur ce lot : {e}")

if __name__ == "__main__":
    for site in SITES:
        if not site["url"] or not site["client"]:
            continue
        print(f"\n=== Traitement du site : {site['url']} ===")
        
        wcapi = API(
            url=site["url"],
            consumer_key=site["client"],
            consumer_secret=site["secret"],
            version="wc/v3",
            timeout=60
        )
        
        products_meta = fetch_products_catalog(wcapi)
        orders = fetch_incremental_orders(wcapi, site["id"])
        recs = calculate_recommendations(orders, products_meta)
        push_to_wordpress_in_batches(recs, site["url"], site["token"])
