import json
import math
import os
from io import BytesIO
from pathlib import Path

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

    def load_dotenv(dotenv_path=None):
        try:
            env_path = Path(dotenv_path) if dotenv_path else BASE_DIR / ".env"
            with open(env_path) as env_file:
                for line in env_file:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
        except FileNotFoundError:
            pass
        return False


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
GRID_RESOLUTION = 120
API_KEY = os.getenv("GEOLOCATION_API_KEY")


class CoordinateSystem:
    _instance = None

    @classmethod
    def create(cls, origin_lat, origin_lon, origin_z=0.0):
        cls._instance = cls(origin_lat, origin_lon, origin_z)
        return cls._instance

    @classmethod
    def get(cls):
        if cls._instance is None:
            raise RuntimeError(
                "CoordinateSystem not initialized. Call create() first."
            )
        return cls._instance

    def __init__(self, origin_lat, origin_lon, origin_z=0.0):
        self.origin_lat = origin_lat
        self.origin_lon = origin_lon
        self.origin_z = origin_z
        epsg, zone = self._get_utm_epsg(origin_lat, origin_lon)
        self.epsg = epsg
        self.zone = zone
        self._transformer = Transformer.from_crs("EPSG:4326", epsg, always_xy=True)
        self.origin_x, self.origin_y = self._transformer.transform(
            origin_lon, origin_lat
        )
        print(f"Detected Location: Lat {origin_lat}, Lon {origin_lon}")
        print(f"Automatically assigning projection to {epsg} (UTM Zone {zone})")

    @staticmethod
    def _get_utm_epsg(lat, lon):
        zone = int(math.floor((lon + 180) / 6) + 1)
        if lat >= 0:
            epsg = f"EPSG:326{zone:02d}"
        else:
            epsg = f"EPSG:327{zone:02d}"
        return epsg, zone

    def to_local(self, lat, lon, elevation=0.0):
        gx, gy = self._transformer.transform(lon, lat)
        return (gx - self.origin_x, gy - self.origin_y, elevation - self.origin_z)

    def to_global(self, x, y):
        lon, lat = self._transformer.transform(
            x + self.origin_x, y + self.origin_y, direction="INVERSE"
        )
        return (lat, lon)


class Route:
    def __init__(self, file_name):
        self.gpx_file = file_name

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
        cs: CoordinateSystem,
        raw_points: list[tuple[float, float]],
        output_json_path: str,
        grid_coords: list[tuple[float, float]],
        terrain_elevations: list[float],
        route_elevations: list[float],
        osm_features=None,
    ):
        json_terrain = []
        for (lat, lon), ele in zip(grid_coords, terrain_elevations, strict=False):
            x, y, z = cs.to_local(lat, lon, ele)
            json_terrain.append({"x": x, "y": y, "z": z})

        json_route = []
        for (lat, lon), ele in zip(raw_points, route_elevations, strict=False):
            x, y, z = cs.to_local(lat, lon, ele)
            json_route.append({"x": x, "y": y, "z": z})

        formatted_features = []
        if osm_features and "elements" in osm_features:
            for el in osm_features["elements"]:
                if el["type"] == "way":
                    feature_type = "unknown"
                    tags = el.get("tags", {})

                    if "building" in tags:
                        feature_type = "building"
                    elif (
                        "natural" in tags
                        and tags["natural"] == "wood"
                        or "landuse" in tags
                        and tags["landuse"] == "forest"
                    ):
                        feature_type = "forest"
                    elif "highway" in tags:
                        feature_type = "road"
                    elif (
                        "waterway" in tags
                        or "natural" in tags
                        and tags["natural"] == "water"
                    ):
                        feature_type = "water"
                    else:
                        continue

                    local_geometry = []
                    for node in el.get("geometry", []):
                        x, y, _ = cs.to_local(node["lat"], node["lon"])
                        local_geometry.append({"x": x, "y": y})

                    if local_geometry:
                        formatted_features.append(
                            {
                                "id": el["id"],
                                "type": feature_type,
                                "tags": tags,
                                "geometry": local_geometry,
                            }
                        )

        # Save compiled asset outputs
        output_data = {
            "grid_size": GRID_RESOLUTION,
            "terrain": json_terrain,
            "route": json_route,
            "features": formatted_features,  # Embedded features for Blender
        }
        with open(output_json_path, "w") as f:
            json.dump(output_data, f, indent=4)
        return output_data


class Map:
    @staticmethod
    def deg_to_tile_xy(lat, lon, zoom):
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
        nw_lat, nw_lon = self.tile_xy_to_deg(x_min, y_min, zoom)
        se_lat, se_lon = self.tile_xy_to_deg(x_max + 1, y_max + 1, zoom)

        grindResult = []
        for row in range(GRID_RESOLUTION):
            lat = nw_lat + (se_lat - nw_lat) * (row / (GRID_RESOLUTION - 1))
            for col in range(GRID_RESOLUTION):
                lon = nw_lon + (se_lon - nw_lon) * (col / (GRID_RESOLUTION - 1))
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
                        msg = f"Elevation API returned {len(items)} items for {len(chunk)} coordinates"
                        raise RuntimeError(msg)
                    elevations.extend([item["elevation"] for item in items])
                else:
                    msg = f"Elevation API request failed with status {response.status_code}: {response.text}"
                    raise RuntimeError(msg)
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
        print(
            f"tiles borders what i get y_start {y_start} , y_end {y_end} , x_start {x_start} , x_end {x_end}"
        )
        total_w = (x_end - x_start + 1) * tile_size
        total_h = (y_end - y_start + 1) * tile_size
        canvas = Image.new("RGB", (total_w, total_h))

        try:
            for ty in range(y_start, y_end + 1):
                for tx in range(x_start, x_end + 1):
                    url = (
                        f"https://api.mapy.com/v1/maptiles/outdoor/256/{zoom}/{tx}/{ty}"
                    )
                    resp = requests.get(url, params={"apikey": API_KEY}, timeout=5.0)
                    resp.raise_for_status()

                    tile = Image.open(BytesIO(resp.content))
                    paste_x = (tx - x_start) * tile_size
                    paste_y = (ty - y_start) * tile_size
                    canvas.paste(tile, (paste_x, paste_y))
        except requests.exceptions.RequestException as e:
            msg = f"Map tile network error: {e}"
            raise RuntimeError(msg) from e
        return canvas


class OSMFeatures:
    @staticmethod
    def fetch_features(x_min, x_max, y_min, y_max, zoom):
        map_inst = Map()
        y_start, y_end = min(y_min, y_max), max(y_min, y_max)
        x_start, x_end = min(x_min, x_max), max(x_min, x_max)
        print(
            f"features borders what i get y_start {y_start} , y_end {y_end} , x_start {x_start} , x_end {x_end}"
        )
        nw_lat, nw_lon = map_inst.tile_xy_to_deg(x_start, y_start, zoom)
        se_lat, se_lon = map_inst.tile_xy_to_deg(x_end + 1, y_end + 1, zoom)

        print(
            f"Fetching OpenStreetMap elements for bbox: ({nw_lat}, {nw_lon}, {se_lat}, {se_lon})"
        )

        overpass_url = "https://overpass.openstreetmap.fr/api/interpreter"
        query = f"""
            [out:json][timeout:90];
            (
              way["building"]({se_lat},{nw_lon},{nw_lat},{se_lon});
              way["highway"]({se_lat},{nw_lon},{nw_lat},{se_lon});
              way["natural"="wood"]({se_lat},{nw_lon},{nw_lat},{se_lon});
              way["landuse"="forest"]({se_lat},{nw_lon},{nw_lat},{se_lon});
              way["natural"="water"]({se_lat},{nw_lon},{nw_lat},{se_lon});
              way["waterway"]({se_lat},{nw_lon},{nw_lat},{se_lon});
            );
            out geom;
            """

        headers = {
            "User-Agent": "blenderMapExperiment (contact: josef.pasek17@gmail.com)",
            "Accept-Encoding": "gzip, deflate",  # Helps speed up data transfer
        }

        try:
            response = requests.post(
                overpass_url, data={"data": query}, headers=headers, timeout=100.0
            )

            if response.status_code == 200:
                return response.json()
            else:
                print(f"Overpass API error: {response.status_code}")
                print(f"Server response text: {response.text}")
                return None

        except Exception as err:
            print(f"Failed fetching OSM features: {err}")
            return None


def main():
    print("Starting GPX processing...")
    route = Route("/home/tjoslef/skola/blender_experiment/Morning_Run.gpx")
    raw_points, route_elevations = route.parse_gpx("output.json")

    map_instance = Map()
    zoom = 16
    x_min, x_max, y_min, y_max = map_instance.bonding_area(raw_points)
    grid_coords = map_instance.gettingGrind(x_min, x_max, y_min, y_max, zoom)
    osm_raw_data = OSMFeatures.fetch_features(x_min, x_max, y_min, y_max, zoom)
    terrain_elevations = map_instance.gettingElevation(grid_coords)

    cs = CoordinateSystem.create(
        grid_coords[0][0], grid_coords[0][1], route_elevations[0]
    )
    route.transformationCord(
        cs,
        raw_points,
        "output.json",
        grid_coords,
        terrain_elevations,
        route_elevations,
        osm_features=osm_raw_data,
    )
    print("Transformation complete, output written to output.json")

    tiles2Dmap = map_instance.getTiles(x_min, x_max, y_min, y_max, zoom)
    tiles2Dmap.save("vysledna_mapa.png")


if __name__ == "__main__":
    main()
