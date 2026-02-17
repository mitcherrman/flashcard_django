import math

# Calculates probability one wins against another
def expected_score(elo_a: float, elo_b: float) -> float:
    return 1 / (1 + 10 ** ((elo_b - elo_a) / 400))

# Controls volatility of player's elo
def k_factor(comparisons, base_k=32, min_k=2): # 32 is starting volatility
    return max(min_k, base_k / math.sqrt(comparisons + 1)) 

def update_elo(a, b, winner_id):
    exp_a = expected_score(a.elo, b.elo) # Expected probabilty of a winning
    score_a = 1 if a.id == winner_id else 0 # A wins set to 1 else 0

    # Controls how much elo changes after match
    # Use min to make sure rating doesn't swing too hard when a new and old card is matched
    k = min(
        k_factor(a.comparisons), # ?delete .comparisons?
        k_factor(b.comparisons)
    )

    # Elo gain/loss formula
    change = int(k * (score_a - exp_a))
    
    # A gains whatever B loses and vice versa
    a.elo += change 
    b.elo -= change
    
    a.comparisons += 1
    b.comparisons += 1