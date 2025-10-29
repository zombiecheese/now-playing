from spotipy import SpotifyOAuth

SPOTIFY_CLIENT_ID = "<client-id>"
SPOTIFY_CLIENT_SECRET = "<client-secret>"
SPOTIFY_REDIRECT_URI = "http://127.0.0.1:8888/callback"
SPOTIFY_SCOPE = "playlist-modify-public playlist-modify-private"

auth = SpotifyOAuth(
    client_id=SPOTIFY_CLIENT_ID,
    client_secret=SPOTIFY_CLIENT_SECRET,
    redirect_uri=SPOTIFY_REDIRECT_URI,
    scope=SPOTIFY_SCOPE,
    open_browser=True
)

token_info = auth.get_access_token(as_dict=False)
print("Access token successfully retrieved and stored in .cache file.")