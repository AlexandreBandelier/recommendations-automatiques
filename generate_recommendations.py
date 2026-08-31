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

def calculate_recommendations(orders):
    """Calcule les paires de produits fréquemment achetés ensemble par SKU."""
    pairs = defaultdict(Counter)
    for order in orders:
        skus = [item['sku'] for item in order.get('line_items', []) if item.get('sku')]
        for i in range(len(skus)):
            for j in range(len(skus)):
                if i != j:
                    pairs[skus[i]][skus[j]] += 1
                    
    recommendations = {}
    for sku, related_counts in pairs.items():
        top_related = [rel_sku for rel_sku, count in related_counts.most_common(3) if count >= 2]
        if top_related:
            recommendations[sku] = top_related
            
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
