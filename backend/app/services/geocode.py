from __future__ import annotations

import json
import requests
from typing import Any
from backend.app.config import get_settings


def google_maps_link(latitude: float | None, longitude: float | None) -> str | None:
    if latitude is None or longitude is None:
        return None
    return f"https://www.google.com/maps?q={latitude},{longitude}"


def geocode_address(address: str | None) -> tuple[float | None, float | None]:
    """Geocode address using Google Maps Geocoding API (preferred) or OpenStreetMap Nominatim (fallback)."""
    if not address:
        return None, None
    
    settings = get_settings()
    
    # 1. Try Google Maps Geocoding if API key is set
    if settings.google_maps_api_key:
        try:
            url = "https://maps.googleapis.com/maps/api/geocode/json"
            response = requests.get(
                url,
                params={"address": address, "key": settings.google_maps_api_key},
                timeout=8
            )
            response.raise_for_status()
            data = response.json()
            if data.get("status") == "OK" and data.get("results"):
                loc = data["results"][0]["geometry"]["location"]
                return float(loc["lat"]), float(loc["lng"])
        except Exception:
            # Silently fall back to OSM on error
            pass

    # 2. Fallback to OpenStreetMap Nominatim
    try:
        response = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"format": "json", "limit": 1, "q": address},
            headers={"User-Agent": settings.geocoder_user_agent},
            timeout=8,
        )
        response.raise_for_status()
        rows = response.json()
        if rows:
            return float(rows[0]["lat"]), float(rows[0]["lon"])
    except Exception:
        pass
        
    return None, None


def score_location(
    address: str | None,
    latitude: float | None,
    longitude: float | None,
    locality: str | None = None,
    district: str | None = None
) -> tuple[float, str]:
    """Score the location quality (1.0 to 10.0) using OpenAI if key is set, else rule-based scoring."""
    settings = get_settings()
    
    address_str = ", ".join(filter(None, [address, locality, district]))
    if not address_str:
        return 3.0, "Minimal location data provided."

    # 1. Try OpenAI-driven scoring if API key is set
    if settings.openai_api_key:
        try:
            from openai import OpenAI
            client = OpenAI(api_key=settings.openai_api_key)
            prompt = f"""
            Analyze the quality and prestige of this real estate location in Rajasthan, India.
            Address: {address_str}
            Coordinates: {latitude or 'Unknown'}, {longitude or 'Unknown'}
            
            Score the location on a scale of 1.0 (very remote/poorly connected) to 10.0 (ultra-premium core area).
            Guidelines:
            - Premium central Jaipur localities like C-Scheme, Civil Lines, Bani Park, Vaishali Nagar, Malviya Nagar, JLN Marg should score high (8.0 to 10.0).
            - Well-connected district headquarters or established suburbs score medium-high (6.0 to 8.0).
            - Rural, remote, or poorly defined addresses score lower (3.0 to 5.0).
            - Having coordinates adds credibility (+0.5).
            
            Return ONLY a single valid JSON object containing exactly two keys:
            - "score": a float between 1.0 and 10.0
            - "reason": a concise explanation of the score in max 100 characters.
            """
            response = client.chat.completions.create(
                model=settings.openai_model,
                messages=[
                    {"role": "system", "content": "You are an expert real estate location analyst."},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"},
                temperature=0.2,
                max_tokens=150
            )
            data = json.loads(response.choices[0].message.content or "{}")
            score = float(data.get("score", 5.0))
            reason = str(data.get("reason", "Location assessed by AI."))
            return max(1.0, min(10.0, score)), reason
        except Exception:
            pass

    # 2. Rule-based Fallback Scoring
    score = 5.0
    reason_parts = []
    
    # Analyze location words
    text = address_str.lower()
    premium_zones = {
        "c-scheme": 9.5,
        "c scheme": 9.5,
        "civil lines": 9.2,
        "civil line": 9.2,
        "bani park": 8.8,
        "banipark": 8.8,
        "vaishali nagar": 8.7,
        "vaishali": 8.7,
        "malviya nagar": 8.6,
        "malviyanagar": 8.6,
        "jln marg": 9.0,
        "mansarovar": 8.0,
        "tonk road": 8.2,
        "shyam nagar": 8.4,
        "c-scheme, jaipur": 9.5,
        "raja park": 8.3,
        "bapu nagar": 8.5
    }
    
    matched_premium = False
    for zone, premium_score in premium_zones.items():
        if zone in text:
            score = premium_score
            reason_parts.append(f"Premium zone match ({zone.title()})")
            matched_premium = True
            break
            
    if not matched_premium:
        # Generic rating booster based on details
        detail_score = 3.0
        if district:
            detail_score += 1.5
        if locality:
            detail_score += 1.5
        if address and len(address) > 15:
            detail_score += 1.0
        if latitude and longitude:
            detail_score += 1.0
        score = min(8.0, detail_score)
        reason_parts.append("Rule-based scoring on address details")
        
    if latitude and longitude and not matched_premium:
        score = min(10.0, score + 0.5)
        
    reason = " | ".join(reason_parts) if reason_parts else "Location verified."
    return round(score, 1), reason
