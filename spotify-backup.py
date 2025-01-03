#!/usr/bin/env python3

import argparse
import codecs
import http.client
import http.server
import json
import logging
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import curses
from typing import Any
from datetime import datetime

logging.basicConfig(level=20, datefmt="%I:%M:%S", format="[%(asctime)s] %(message)s")


class SpotifyAPI:

    # Requires an OAuth token.
    def __init__(self, auth):
        self._auth = auth

    # Gets a resource from the Spotify API and returns the object.
    def get(self, url, params={}, tries=3):
        # Construct the correct URL.
        if not url.startswith("https://api.spotify.com/v1/"):
            url = "https://api.spotify.com/v1/" + url
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)

        # Try the sending off the request a specified number of times before giving up.
        for _ in range(tries):
            try:
                req = urllib.request.Request(url)
                req.add_header("Authorization", "Bearer " + self._auth)
                res = urllib.request.urlopen(req)
                reader = codecs.getreader("utf-8")
                return json.load(reader(res))
            except Exception as err:
                logging.info("Couldn't load URL: %s (%s)", url, err)
                time.sleep(2)
                logging.info("Trying again...")
        sys.exit(1)

    # The Spotify API breaks long lists into multiple pages. This method automatically
    # fetches all pages and joins them, returning in a single list of objects.
    def list(self, url, params={}):
        last_log_time = time.time()
        response = self.get(url, params)
        items = response["items"]

        while response["next"]:
            if time.time() > last_log_time + 15:
                last_log_time = time.time()
                logging.info("Loaded %d/%d items", len(items), response["total"])

            response = self.get(response["next"])
            items += response["items"]
        return items

    # Pops open a browser window for a user to log in and authorize API access.
    @staticmethod
    def authorize(client_id, scope):
        url: str = "https://accounts.spotify.com/authorize?" + urllib.parse.urlencode(
            {
                "response_type": "token",
                "client_id": client_id,
                "scope": scope,
                "redirect_uri": "http://127.0.0.1:{}/redirect".format(
                    SpotifyAPI._SERVER_PORT
                ),
            }
        )
        logging.info("Logging in (click if it doesn't open automatically): %s", url)
        webbrowser.open(url)

        # Start a simple, local HTTP server to listen for the authorization token... (i.e. a hack).
        server = SpotifyAPI._AuthorizationServer("127.0.0.1", SpotifyAPI._SERVER_PORT)
        try:
            while True:
                server.handle_request()
        except SpotifyAPI._Authorization as auth:
            return SpotifyAPI(auth.access_token)

    # The port that the local server listens on. Don't change this,
    # as Spotify only will redirect to certain predefined URLs.
    _SERVER_PORT = 43019

    class _AuthorizationServer(http.server.HTTPServer):
        def __init__(self, host, port):
            http.server.HTTPServer.__init__(
                self, (host, port), SpotifyAPI._AuthorizationHandler
            )

        # Disable the default error handling.
        def handle_error(self, request, client_address):
            raise

    class _AuthorizationHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            # The Spotify API has redirected here, but access_token is hidden in the URL fragment.
            # Read it using JavaScript and send it to /token as an actual query string...
            if self.path.startswith("/redirect"):
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(
                    b'<script>location.replace("token?" + location.hash.slice(1));</script>'
                )

            # Read access_token and use an exception to kill the server listening...
            elif self.path.startswith("/token?"):
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(
                    b"<script>close()</script>Thanks! You may now close this window."
                )

                access_token = re.search("access_token=([^&]*)", self.path).group(1)
                logging.info("Received access token from Spotify: %s", access_token)
                raise SpotifyAPI._Authorization(access_token)

            else:
                self.send_error(404)

        # Disable the default logging.
        def log_message(self, format, *args):
            pass

    class _Authorization(Exception):
        def __init__(self, access_token: str):
            self.access_token = access_token


def tui_select_playlists(playlists):
    """
    Text-based user interface to select playlists.
    """
    selected = []

    def curses_main(stdscr):
        nonlocal selected
        curses.curs_set(0)
        current_row = 0

        while True:
            stdscr.clear()
            stdscr.addstr(
                0,
                0,
                "Select playlists to download (press SPACE to toggle, ENTER to confirm, + to move up, - to move down):",
                curses.A_BOLD,
            )

            for idx, playlist in enumerate(playlists):
                marker = "[X]" if playlist in selected else "[ ]"
                if idx == current_row:
                    stdscr.addstr(
                        idx + 2, 2, f"> {marker} {playlist['name']}", curses.A_REVERSE
                    )
                else:
                    stdscr.addstr(idx + 2, 2, f"  {marker} {playlist['name']}")

            key = stdscr.getch()

            if key == curses.KEY_UP and current_row > 0:
                current_row -= 1
            elif key == curses.KEY_DOWN and current_row < len(playlists) - 1:
                current_row += 1
            elif key == ord(" "):  # Toggle selection
                if playlists[current_row] in selected:
                    selected.remove(playlists[current_row])
                else:
                    selected.append(playlists[current_row])
            elif key == ord("+") and current_row > 0:  # Move current playlist up
                # Swap playlists in the array
                playlists[current_row], playlists[current_row - 1] = playlists[current_row - 1], playlists[current_row]
                current_row -= 1
                # Reorder the selected list to match the new order
                selected = [p for p in playlists if p in selected]
            elif key == ord("-") and current_row < len(playlists) - 1:  # Move current playlist down
                # Swap playlists in the array
                playlists[current_row], playlists[current_row + 1] = playlists[current_row + 1], playlists[current_row]
                current_row += 1
                # Reorder the selected list to match the new order
                selected = [p for p in playlists if p in selected]
            elif key == ord("\n"):  # Enter key
                break

            stdscr.refresh()

    curses.wrapper(curses_main)
    return selected


def main():
    # Parse arguments.
    parser = argparse.ArgumentParser(
        description="Exports your Spotify playlists. By default, opens a browser window "
        + "to authorize the Spotify Web API, but you can also manually specify"
        + " an OAuth token with the --token option."
    )
    parser.add_argument(
        "--token",
        metavar="OAUTH_TOKEN",
        help="use a Spotify OAuth token (requires the "
        + "`playlist-read-private` permission)",
    )
    parser.add_argument(
        "--dump",
        default="playlists",
        choices=["liked,playlists", "playlists,liked", "playlists", "liked"],
        help="dump playlists or liked songs, or both (default: playlists)",
    )
    parser.add_argument(
        "--format",
        default="txt",
        choices=["json", "txt"],
        help="output format (default: txt)",
    )
    parser.add_argument("file", help="output filename", nargs="?")
    args = parser.parse_args()

    # If they didn't give a filename, then just prompt them. (They probably just double-clicked.)
    while not args.file:
        args.file = input("Enter a file name (e.g. playlists.txt): ")
        args.format = args.file.split(".")[-1]

    # Log into the Spotify API.
    if args.token:
        spotify = SpotifyAPI(args.token)
    else:
        spotify = SpotifyAPI.authorize(
            client_id="5c098bcc800e45d49e476265bc9b6934",
            scope="playlist-read-private playlist-read-collaborative user-library-read",
        )

    # Get the ID of the logged in user.
    logging.info("Loading user info...")
    me = spotify.get("me")
    logging.info("Logged in as %(display_name)s (%(id)s)", me)

    playlists: list[dict[str, Any]] = []
    liked_albums: list[dict[str, Any]] = []

    # List liked albums and songs
    if "liked" in args.dump:
        logging.info("Loading liked albums and songs...")
        liked_tracks = spotify.list("me/tracks", {"limit": 50})
        liked_albums = spotify.list("me/albums", {"limit": 50})
        playlists += [{"name": "Liked Songs", "tracks": liked_tracks}]

    # List all playlists and the tracks in each playlist
    if "playlists" in args.dump:
        logging.info("Loading playlists...")
        playlist_data = spotify.list(
            "users/{user_id}/playlists".format(user_id=me["id"]), {"limit": 50}
        )
        playlist_data.reverse()
        logging.info("Found %d playlists", len(playlist_data))

        # TUI for selecting playlists
        selected_playlists = tui_select_playlists(playlist_data)

        # If no playlists were selected, log and exit
        if not selected_playlists:
            logging.info("No playlists selected in TUI. Adding all playlists.")
            sys.exit(1)

        # List all tracks in each selected playlist
        for playlist in selected_playlists:
            logging.info(
                "Loading playlist: %s (%d song(s))",
                playlist["name"],
                playlist["tracks"]["total"],
            )
            playlist["tracks"] = spotify.list(
                playlist["tracks"]["href"], {"limit": 100}
            )

            # Get playlist creation date and add one second
            playlist_creation_date = datetime.fromisoformat(playlist.get("created_at", "1970-01-01T00:00:00Z").replace("Z", "+00:00"))

            # Sort by 'added_at' or default to playlist_creation_date + 1 second
            playlist["tracks"].sort(
                key=lambda track: datetime.fromisoformat(track["added_at"].replace("Z", "+00:00")) 
                if track.get("added_at") else playlist_creation_date,
                reverse=True
            )

        playlists += selected_playlists

    # Write the file.
    logging.info("Writing files...")
    with open(args.file, "w", encoding="utf-8") as f:
        # JSON file.
        if args.format == "json":
            json.dump({"playlists": playlists, "albums": liked_albums}, f)

        # Tab-separated file.
        else:
            f.write("Playlists: \r\n\r\n")
            for playlist in playlists:
                f.write(playlist["name"] + "\r\n")
                for track in playlist["tracks"]:
                    if track["track"] is None:
                        continue
                    f.write(
                        "{name}\t{artists}\t{album}\t{uri}\t{release_date}\r\n".format(
                            uri=track["track"]["uri"],
                            name=track["track"]["name"],
                            artists=", ".join(
                                [artist["name"] for artist in track["track"]["artists"]]
                            ),
                            album=track["track"]["album"]["name"],
                            release_date=track["track"]["album"]["release_date"],
                        )
                    )
                f.write("\r\n")
            if len(liked_albums) > 0:
                f.write("Liked Albums: \r\n\r\n")
                for album in liked_albums:
                    uri = album["album"]["uri"]
                    name = album["album"]["name"]
                    artists = ", ".join(
                        [artist["name"] for artist in album["album"]["artists"]]
                    )
                    release_date = album["album"]["release_date"]
                    album = f"{artists} - {name}"

                    f.write(f"{name}\t{artists}\t-\t{uri}\t{release_date}\r\n")

    logging.info("Wrote file: %s", args.file)


if __name__ == "__main__":
    main()
