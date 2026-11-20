"""Clima (Etapa 7): provedores desacoplados (INMET, MET Norway, Open-Meteo) atrás de uma interface mínima.

Uso: ``WeatherService.from_settings(settings).lookup(lat, lon, ibge=..., tz=...)`` → resposta do contrato A.
Nada é buscado na importação nem na criação do serviço.
"""
from .base import NO_DATA_MESSAGE, HttpResponse, Location, Provider, ProviderError, WeatherConfig, urllib_fetch
from .service import PROVIDERS, SIMULATED_NOTE, WeatherService

__all__ = ["NO_DATA_MESSAGE", "HttpResponse", "Location", "Provider", "ProviderError", "WeatherConfig", "urllib_fetch",
           "PROVIDERS", "SIMULATED_NOTE", "WeatherService"]
