import os
import json
import datetime
import requests
from woocommerce import API
from collections import defaultdict, Counter

# Nettoyage de l'URL pour éviter les problèmes de double slash
WOO_URL = os.environ.get("WOO_URL", "").rstrip("/")
WOO_CLIENT = os.environ.get("WOO_CLIENT")
WOO_SECRET = os.environ.get("WOO_SECRET")
RECS_API_TOKEN = os.environ.get("RECS_API_TOKEN")

# Connexion à l'API WooCommerce
wcapi = API(
    url=WOO_URL,
    consumer_CLIENT=WOO_CLIENT,
    consumer_secret=WOO_SECRET,
    version="wc/v3",
    timeout=60
)

def fetch_completed_orders():
    """Récupère l'historique des commandes terminées avec pagination dynamique."""
    orders = []
    page = 1
    print("Récupération des commandes WooCommerce...")
    
    while True:
        try:
            response = wcapi.get("orders", params={"per_page": 100, "page": page, "status": "completed"})
            if response.status_code != 200:
                print(f"Erreur lors de la récupération (page {page}) : HTTP {response.status_code}")
                break
            
            data = response.json()
            if not data or not isinstance(data, list):
                break
            
            orders.extend(data)
            
            # Récupération du nombre total de pages via les en-têtes WooCommerce/WordPress
            total_pages = int(response.headers.get("X-WP-TotalPages", page))
            if page >= total_pages or len(data) < 100:
                break
            page += 1
        except Exception as e:
            print(f"Exception rencontrée lors de la récupération des commandes : {e}")
            break

    print(f"{len(orders)} commandes récupérées au total.")
    return orders

def calculate_recommendations_with_rules(orders, products_metadata):
    """
    Calcule les cross-sells avec des règles strictes de compatibilité et de volume.
    """
    pairs = defaultdict(Counter)
    
    # 1. Comptage des co-occurrences dans les commandes
    for order in orders:
        skus = [item['sku'] for item in order.get('line_items', []) if item.get('sku')]
        for i in range(len(skus)):
            for j in range(len(skus)):
                if i != j:
                    pairs[skus[i]][skus[j]] += 1

    recommendations = {}

    for main_sku, related_counts in pairs.items():
        main_meta = products_metadata.get(main_sku, {})
        main_category = main_meta.get('category')
        
        valid_recs = []

        # 2. Filtrage par règles métier
        for rel_sku, count in related_counts.most_common(10):
            rel_meta = products_metadata.get(rel_sku, {})
            rel_category = rel_meta.get('category')

            # Règle Kobudo : uniquement du Kobudo
            if main_category == "Kobudo" and rel_category != "Kobudo":
                continue

            # Règle Kata : pas de gants ou protège-tibias
            if "kata" in main_meta.get('name', '').lower() and "gant" in rel_meta.get('name', '').lower():
                continue

            valid_recs.append(rel_sku)
            if len(valid_recs) == 4:  # Max 4 cross-sells
                break

        # 3. Quota minimum : si moins de 3 recommendations, compléter avec la même catégorie
        if len(valid_recs) < 3 and main_category:
            fallback_skus = [
                sku for sku, meta in products_metadata.items()
                if meta.get('category') == main_category and sku != main_sku and sku not in valid_recs
            ]
            valid_recs.extend(fallback_skus[:(4 - len(valid_recs))])

        # Enregistrement final si on a au moins le quota requis
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
        print(f"Erreur lors de l'envoie vers WordPress : {e}")

if __name__ == "__main__":
    orders = fetch_completed_orders()
    recs = calculate_recommendations(orders)
    batch = get_current_batch(recs)
    push_to_wordpress(batch)
