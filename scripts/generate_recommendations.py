import os
import datetime
import requests
from woocommerce import API
from collections import defaultdict, Counter

# Configuration et Nettoyage de l'URL
WOO_URL = os.environ.get("WOO_URL", "").rstrip("/")
WOO_CLIENT = os.environ.get("WOO_CLIENT")
WOO_SECRET = os.environ.get("WOO_SECRET")
RECS_API_TOKEN = os.environ.get("RECS_API_TOKEN")

# Connexion à l'API WooCommerce
wcapi = API(
    url=WOO_URL,
    consumer_key=WOO_CLIENT,
    consumer_secret=WOO_SECRET,
    version="wc/v3",
    timeout=60
)

# Liste des marques courantes en arts martiaux pour la détection
KNOWN_BRANDS = ["tokaido", "adidas", "arawaza", "venum", "kamikaze", "kaze", "hayashi", "shureido", "budo-nord"]

def fetch_products_catalog():
    """Récupère l'ensemble du catalogue pour appliquer les règles sur les catégories, le prix et le stock."""
    products_meta = {}
    page = 1
    print("Chargement des métadonnées du catalogue produits (Catégories, Prix, Stocks)...")
    
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
            print(f"Erreur chargement catalogue : {e}")
            break

    print(f"Métadonnées chargées pour {len(products_meta)} produits.")
    return products_meta

def fetch_completed_orders():
    """Récupère une tranche de l'historique (depuis 2018) en fonction du jour."""
    orders = []
    page = 1
    
    start_year = 2018
    current_year = datetime.datetime.now().year
    day_of_year = datetime.datetime.now().timetuple().tm_yday
    
    years_range = list(range(start_year, current_year + 1))
    chunk_size = max(1, len(years_range) // 5)
    slice_idx = day_of_year % 5
    
    selected_years = years_range[slice_idx * chunk_size : (slice_idx + 1) * chunk_size]
    if not selected_years:
        selected_years = [current_year]
        
    after_date = f"{selected_years[0]}-01-01T00:00:00"
    before_date = f"{selected_years[-1]}-12-31T23:59:59"
    
    print(f"Analyse de l'historique pour la période : {selected_years[0]} à {selected_years[-1]}...")

    while True:
        try:
            params = {
                "per_page": 100,
                "page": page,
                "status": "completed",
                "after": after_date,
                "before": before_date
            }
            response = wcapi.get("orders", params=params)
            
            if response.status_code != 200:
                print(f"Fin de la récupération (HTTP {response.status_code})")
                break
            
            data = response.json()
            if not data or not isinstance(data, list):
                break
            
            orders.extend(data)
            
            if len(data) < 100:
                break
            page += 1
        except Exception as e:
            print(f"Exception lors de la récupération : {e}")
            break

    print(f"Total récupéré pour cette tranche : {len(orders)} commandes.")
    return orders

def check_category_compatibility(main_meta, rel_meta):
    """
    Vérification stricte basée sur les catégories formelles et mots-clés du titre.
    """
    # [OPTIMISATION 1] : Ne jamais recommander un produit hors stock
    if rel_meta.get("stock_status") != "instock":
        return False

    m_cats = set(c for c in main_meta.get("categories", []))
    r_cats = set(c for c in rel_meta.get("categories", []))
    m_name = main_meta.get("name", "")
    r_name = rel_meta.get("name", "")

    # 1. Isolation stricte des disciplines
    strict_disciplines = {"kobudo", "yoseikan", "yoseikan budo", "nanbudo"}
    m_strict = m_cats.intersection(strict_disciplines)
    r_strict = r_cats.intersection(strict_disciplines)
    if m_strict or r_strict:
        if m_strict != r_strict:
            return False

    # 2. Kata vs Protections
    is_m_kata = any("kata" in c for c in m_cats) or "kata" in m_name
    is_r_protection = any(c in r_cats for c in ["protections", "gants", "protège-tibias", "casques", "plastrons"])
    if is_m_kata and is_r_protection:
        return False

    # 3. Enfants vs Adultes
    is_m_kid = any(kw in m_name or any(kw in c for c in m_cats) for kw in ["enfant", "junior", "kid"])
    is_r_kid = any(kw in r_name or any(kw in c for c in r_cats) for kw in ["enfant", "junior", "kid"])
    neutral_categories = {"sacs", "bagagerie", "accessoires", "matériel club", "soin", "ceintures"}
    if is_m_kid != is_r_kid:
        if not r_cats.intersection(neutral_categories):
            return False

    # 4. Niveau : Initiation vs WKF Compétition
    is_m_comp = any(kw in m_name or any(kw in c for c in m_cats) for kw in ["wkf", "compétition", "competition"])
    is_r_init = any(kw in r_name or any(kw in c for c in r_cats) for kw in ["initiation", "débutant", "debutant"])
    if is_m_comp and is_r_init:
        return False

    # 5. Non-redondance (pas la même sous-catégorie exacte sauf consommables)
    if m_cats == r_cats and not any(c in m_cats for c in ["accessoires", "ceintures", "soin"]):
        return False

    # [OPTIMISATION 2] : Isolation Homme / Femme
    is_m_fem = "femme" in m_name or "féminin" in m_name or "feminin" in m_name or "fille" in m_name
    is_m_hom = "homme" in m_name or "masculin" in m_name or "garçon" in m_name
    is_r_fem = "femme" in r_name or "féminin" in r_name or "feminin" in r_name or "fille" in r_name
    is_r_hom = "homme" in r_name or "masculin" in r_name or "garçon" in r_name
    
    if (is_m_fem and is_r_hom) or (is_m_hom and is_r_fem):
        return False

    return True

def calculate_recommendations(orders, products_meta):
    """Calcule les paires et remplit jusqu'à 4 recommandations intelligentes."""
    pairs = defaultdict(Counter)
    
    for order in orders:
        skus = [item['sku'] for item in order.get('line_items', []) if item.get('sku')]
        for i in range(len(skus)):
            for j in range(len(skus)):
                if i != j:
                    pairs[skus[i]][skus[j]] += 1

    recommendations = {}

    for main_sku, related_counts in pairs.items():
        if main_sku not in products_meta:
            continue
            
        main_meta = products_meta[main_sku]
        valid_recs = []

        # Analyse du comportement d'achat
        for rel_sku, count in related_counts.most_common(15):
            if rel_sku not in products_meta or rel_sku == main_sku:
                continue
            
            rel_meta = products_meta[rel_sku]
            
            # Règle d'or : On valide si + de 2 commandes communes OU si le filtrage strict est respecté
            if count >= 2 or check_category_compatibility(main_meta, rel_meta):
                valid_recs.append(rel_sku)
                if len(valid_recs) == 4:
                    break

        # Fallback intelligent si l'historique ne suffit pas (< 3 produits)
        if len(valid_recs) < 3:
            main_cats = set(main_meta.get("categories", []))
            main_price = main_meta.get("price", 0.0)
            
            fallback_candidates = []

            for fallback_sku, fallback_meta in products_meta.items():
                if fallback_sku == main_sku or fallback_sku in valid_recs:
                    continue
                
                fallback_cats = set(fallback_meta.get("categories", []))
                
                # Vérification croisée des catégories et des règles
                if main_cats.intersection(fallback_cats) and check_category_compatibility(main_meta, fallback_meta):
                    score = 0
                    
                    # [OPTIMISATION 3.A] : Affinité de Marque (Brand Matching)
                    main_brand = next((b for b in KNOWN_BRANDS if b in main_meta["name"]), None)
                    fallback_brand = next((b for b in KNOWN_BRANDS if b in fallback_meta["name"]), None)
                    if main_brand and main_brand == fallback_brand:
                        score += 50  # Énorme bonus pour recommander la même marque

                    # [OPTIMISATION 3.B] : Cohérence de Prix / Up-Sell Naturel
                    fb_price = fallback_meta.get("price", 0.0)
                    if main_price > 0:
                        ratio = fb_price / main_price
                        if 0.8 <= ratio <= 1.3:
                            score += 20  # Prix similaire à légèrement plus cher (idéal)
                        elif ratio > 1.5:
                            score -= 10  # Pénalité si beaucoup trop cher

                    fallback_candidates.append((score, fallback_sku))

            # Trier les candidats par score de pertinence décroissant
            fallback_candidates.sort(key=lambda x: x[0], reverse=True)
            
            for score, f_sku in fallback_candidates:
                valid_recs.append(f_sku)
                if len(valid_recs) == 4:
                    break

        if len(valid_recs) >= 3:
            recommendations[main_sku] = valid_recs

    return recommendations

def get_current_batch(recommendations):
    """Filtre les recommandations pour n'envoyer que 10 % du catalogue selon le jour."""
    all_skus = sorted(list(recommendations.keys()))
    total_products = len(all_skus)
    
    if total_products == 0:
        return {}

    day_of_year = datetime.datetime.now().timetuple().tm_yday
    batch_index = (day_of_year // 3) % 10
    
    chunk_size = max(1, total_products // 10)
    start_idx = batch_index * chunk_size
    end_idx = start_idx + chunk_size if batch_index < 9 else total_products
    
    batch_skus = all_skus[start_idx:end_idx]
    batch_recs = {sku: recommendations[sku] for sku in batch_skus}
    
    print(f"Lot actuel : {batch_index + 1}/10 | Produits à mettre à jour : {len(batch_recs)} / {total_products}")
    return batch_recs

def push_to_wordpress(batch_recs):
    """Envoie le lot de recommandations à l'endpoint API de WordPress."""
    if not batch_recs:
        print("Aucune recommandation à envoyer aujourd'hui.")
        return

    url = f"{WOO_URL}/wp-json/custom/v1/update-recommendations"
    headers = {
        "Content-Type": "application/json",
        "X-Recommendation-Token": RECS_API_TOKEN
    }
    
    try:
        response = requests.post(url, json=batch_recs, headers=headers, timeout=60)
        response.raise_for_status()
        print(f"Succès — Réponse WordPress ({response.status_code}) : {response.text}")
    except requests.exceptions.RequestException as e:
        print(f"Erreur lors de l'envoi vers WordPress : {e}")

if __name__ == "__main__":
    products_meta = fetch_products_catalog() # Nécessaire pour avoir les catégories, les stocks et les prix
    orders = fetch_completed_orders()
    recs = calculate_recommendations(orders, products_meta)
    batch = get_current_batch(recs)
    push_to_wordpress(batch)
