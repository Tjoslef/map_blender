import json
import math
import os
from io import BytesIO

import requests
from PIL import Image
from pyproj import Transformer

try:
    import defusedxml.ElementTree as et
except ModuleNotFoundError:
    import xml.etree.ElementTree as et

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:

    def load_dotenv():
        try:
            with open(".env") as env_file:
                for line in env_file:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
        except FileNotFoundError:
            pass
        return False


load_dotenv()
GRID_RESOLUTION = 20
API_KEY = os.getenv("GEOLOCATION_API_KEY")


class Route:
    def __init__(self, file_name):
        self.gpx_file = file_name

    def get_utm_epsg(self, lat, lon):
        zone_number = int(math.floor((lon + 180) / 6) + 1)

        if lat >= 0:
            epsg_code = f"EPSG:326{zone_number:02d}"
        else:
            epsg_code = f"EPSG:327{zone_number:02d}"

        return epsg_code, zone_number

    def parse_gpx(self, output_json_path):
        tree = et.parse(self.gpx_file)
        root = tree.getroot()

        raw_points: list[tuple[float, float]] = []
        route_elevations: list[float] = []
        for trkpt in root.findall(".//{*}trkpt"):
            lat = float(trkpt.attrib["lat"])
            lon = float(trkpt.attrib["lon"])
            ele = trkpt.find("{*}ele")
            if ele is None or ele.text is None:
                msg = "GPX track point is missing an <ele> value"
                raise ValueError(msg)
            raw_points.append((lat, lon))
            route_elevations.append(float(ele.text))
        return raw_points, route_elevations

    def transformationCord(
        self,
        raw_points: list[tuple[float, float]],
        output_json_path: str,
        grid_coords: list[tuple[float, float]],
        terrain_elevations: list[float],
        route_elevations: list[float],
    ):

        start_lat, start_lon = raw_points[0]
        epsg_target, zone = self.get_utm_epsg(start_lat, start_lon)
        print(f"Detected Location: Lat {start_lat}, Lon {start_lon}")
        print(f"Automatically assigning projection to {epsg_target} (UTM Zone {zone})")

        transformer = Transformer.from_crs("EPSG:4326", epsg_target, always_xy=True)
        processed_coords = []
        start_x, start_y = transformer.transform(start_lon, start_lat)
        start_z = route_elevations[0]
        # 4. Format Flat Terrain Output Matrix
        json_terrain = []
        for (lat, lon), ele in zip(grid_coords, terrain_elevations, strict=False):
            gx, gy = transformer.transform(lon, lat)
            json_terrain.append(
                {"x": gx - start_x, "y": gy - start_y, "z": ele - start_z}
            )

        # 5. Format Route Path Output
        json_route = []
        for (lat, lon), ele in zip(raw_points, route_elevations, strict=False):
            gx, gy = transformer.transform(lon, lat)
            json_route.append(
                {"x": gx - start_x, "y": gy - start_y, "z": ele - start_z}
            )

        # Save compiled asset outputs
        output_data = {
            "grid_size": GRID_RESOLUTION,
            "terrain": json_terrain,
            "route": json_route,
        }
        with open(output_json_path, "w") as f:
            json.dump(output_data, f, indent=4)
        return processed_coords


class Map:
    @staticmethod
    def deg_to_tile_xy(lat, lon, zoom):
        """Converts lat/lon degrees to standard Web Mercator tile X and Y indices."""
        lat_rad = math.radians(lat)
        n = 2.0**zoom
        x = int((lon + 180.0) / 360.0 * n)
        y = int(
            (1.0 - math.log(math.tan(lat_rad) + (1 / math.cos(lat_rad))) / math.pi)
            / 2.0
            * n
        )
        return x, y

    @staticmethod
    def tile_xy_to_deg(x, y, zoom):
        n = 2.0**zoom
        lon = x / n * 360.0 - 180.0
        lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
        return lat, lon

    def bonding_area(self, raw_points: list[tuple[float, float]]):
        zoom = 16
        min_lat = min(raw_points, key=lambda x: x[0])[0]
        max_lat = max(raw_points, key=lambda x: x[0])[0]

        min_lon = min(raw_points, key=lambda x: x[1])[1]
        max_lon = max(raw_points, key=lambda x: x[1])[1]
        start_x, start_y = self.deg_to_tile_xy(max_lat, min_lon, zoom)
        end_x, end_y = self.deg_to_tile_xy(min_lat, max_lon, zoom)

        x_min, x_max = min(start_x, end_x), max(start_x, end_x)
        y_min, y_max = min(start_y, end_y), max(start_y, end_y)
        return x_min, x_max, y_min, y_max

    def gettingGrind(self, x_min, x_max, y_min, y_max, zoom):
        grindResult: list[tuple[float, float]] = []
        for x in range(GRID_RESOLUTION):
            tile_y = y_min + (y_max - y_min) * (x / (GRID_RESOLUTION - 1))
            for y in range(GRID_RESOLUTION):
                tile_x = x_min + (x_max - x_min) * (y / (GRID_RESOLUTION - 1))
                lat, lon = self.tile_xy_to_deg(tile_x, tile_y, zoom)
                grindResult.append((lat, lon))
        return grindResult

    def gettingElevation(self, coords):
        if not API_KEY:
            msg = "GEOLOCATION_API_KEY is missing; cannot fetch terrain elevations"
            raise RuntimeError(msg)
        elevations = []
        for i in range(0, len(coords), 256):
            chunk = coords[i : i + 256]
            positions_str = ";".join([f"{lon},{lat}" for lat, lon in chunk])
            url = "https://api.mapy.cz/v1/elevation"
            try:
                response = requests.get(
                    url,
                    params={"positions": positions_str, "apikey": API_KEY},
                    timeout=5.0,
                )
                if response.status_code == 200:
                    items = response.json().get("items", [])
                    if len(items) != len(chunk):
                        msg = (
                            "Elevation API returned "
                            f"{len(items)} items for {len(chunk)} coordinates"
                        )
                        raise RuntimeError(msg)
                    elevations.extend([item["elevation"] for item in items])
                else:
                    msg = (
                        "Elevation API request failed with status "
                        f"{response.status_code}: {response.text}"
                    )
                    raise RuntimeError(msg)
            except requests.exceptions.Timeout:
                msg = "Elevation API request timed out"
                raise RuntimeError(msg) from None
            except requests.exceptions.RequestException as e:
                msg = f"Elevation API network error: {e}"
                raise RuntimeError(msg) from e
        return elevations

    def getTiles(self, x_min, x_max, y_min, y_max, zoom):
        if not API_KEY:
            msg = "GEOLOCATION_API_KEY is missing; cannot fetch map tiles"
            raise RuntimeError(msg)
        tile_size = 256
        y_start, y_end = min(y_min, y_max), max(y_min, y_max)
        x_start, x_end = min(x_min, x_max), max(x_min, x_max)

        total_w = (x_end - x_start + 1) * tile_size
        total_h = (y_end - y_start + 1) * tile_size
        canvas = Image.new("RGB", (total_w, total_h))

        try:
            for ty in range(y_start, y_end + 1):
                for tx in range(x_start, x_end + 1):
                    url = (
                        f"https://api.mapy.com/v1/maptiles/outdoor/256/{zoom}/{tx}/{ty}"
                    )
                    resp = requests.get(
                        url,
                        params={"apikey": API_KEY},
                        timeout=5.0,
                    )
                    resp.raise_for_status()

                    tile = Image.open(BytesIO(resp.content))
                    paste_x = (tx - x_start) * tile_size
                    paste_y = (ty - y_start) * tile_size
                    canvas.paste(tile, (paste_x, paste_y))

        except requests.exceptions.Timeout:
            msg = f"Map tile request timed out"
            raise RuntimeError(msg) from None
        except requests.exceptions.RequestException as e:
            msg = f"Map tile network error: {e}"
            raise RuntimeError(msg) from e

        return canvas


def main():
    print("Starting GPX processing...")
    route = Route("/home/tjoslef/skola/blender_experiment/Morning_Run.gpx")
    raw_points, route_elevations = route.parse_gpx("output.json")
    print(f"Parsed {len(raw_points)} raw points")
    with open("tmp_raw_points.json", "w") as f:
        json.dump(raw_points, f)

    map_instance = Map()
    zoom = 16
    x_min, x_max, y_min, y_max = map_instance.bonding_area(raw_points)
    print(f"Bounding area: x=({x_min}, {x_max}), y=({y_min}, {y_max})")
    with open("tmp_bounds.json", "w") as f:
        json.dump({"x_min": x_min, "x_max": x_max, "y_min": y_min, "y_max": y_max}, f)

    grid_coords = map_instance.gettingGrind(x_min, x_max, y_min, y_max, zoom)
    print(f"Generated {len(grid_coords)} grid coordinates")
    with open("tmp_grid_coords.json", "w") as f:
        json.dump(grid_coords, f)

    terrain_elevations = map_instance.gettingElevation(grid_coords)
    print(f"Fetched {len(terrain_elevations)} terrain elevations")
    with open("tmp_terrain_elevations.json", "w") as f:
        json.dump(terrain_elevations, f)

    print(f"Loaded {len(route_elevations)} route elevations from GPX")
    with open("tmp_route_elevations.json", "w") as f:
        json.dump(route_elevations, f)

    route.transformationCord(
        raw_points, "output.json", grid_coords, terrain_elevations, route_elevations
    )
    print("Transformation complete, output written to output.json")
    tiles2Dmap = map_instance.getTiles(x_min, x_max, y_min, y_max, zoom)
    tiles2Dmap.save("vysledna_mapa.png")


if __name__ == "__main__":
    main()
