import os
import json
import datetime
import requests
from woocommerce import API
from collections import defaultdict, Counter

# Nettoyage de l'URL
WOO_URL = os.environ.get("WOO_URL", "").rstrip("/")
WOO_CLIENT = os.environ.get("WOO_CLIENT")
WOO_SECRET = os.environ.get("WOO_SECRET")
RECS_API_TOKEN = os.environ.get("RECS_API_TOKEN")

# Connexion à l'API WooCommerce (consumer_key attend la valeur de WOO_CLIENT)
wcapi = API(
    url=WOO_URL,
    consumer_key=WOO_CLIENT,
    consumer_secret=WOO_SECRET,
    version="wc/v3",
    timeout=60
)

def fetch_completed_orders():
    """Récupère une tranche de l'historique (depuis 2018) en fonction du jour pour couvrir 100% du passé sans dépasser le temps limite."""
    orders = []
    page = 1
    
    # Années à couvrir depuis 2018
    start_year = 2018
    current_year = datetime.datetime.now().year
    
    # Alternance des périodes basées sur le jour de l'année
    day_of_year = datetime.datetime.now().timetuple().tm_yday
    years_range = list(range(start_year, current_year + 1))
    
    # On découpe les années en 5 tranches historiques
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
            print(f"Page {page} récupérée ({len(data)} commandes)")
            
            if len(data) < 100:
                break
            page += 1
        except Exception as e:
            print(f"Exception lors de la récupération : {e}")
            break

    print(f"Total récupéré pour cette tranche : {len(orders)} commandes.")
    return orders
def calculate_recommendations(orders):
    """Calcul basique des recommandations par co-occurrence."""
    pairs = defaultdict(Counter)
    for order in orders:
        skus = [item['sku'] for item in order.get('line_items', []) if item.get('sku')]
        for i in range(len(skus)):
            for j in range(len(skus)):
                if i != j:
                    pairs[skus[i]][skus[j]] += 1
                    
    recommendations = {}
    for sku, related_counts in pairs.items():
        top_related = [rel_sku for rel_sku, count in related_counts.most_common(4) if count >= 2]
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
        print(f"Erreur lors de l'envoi vers WordPress : {e}")

if __name__ == "__main__":
    orders = fetch_completed_orders()
    recs = calculate_recommendations(orders)
    batch = get_current_batch(recs)
    push_to_wordpress(batch)
