// The members' hub (see README). One file draws every page: the address says
// which, and moving between pages changes the address without loading anything
// again. Caddy answers every address that is not a file with the same page, so
// an address can be opened, reloaded or shared.
"use strict";

const view = document.getElementById("view");
const NS = "http://www.w3.org/2000/svg";
const number = new Intl.NumberFormat("en");
const regions = new Intl.DisplayNames(["en"], { type: "region" });
const custom = (p, key) => p.stats.custom[key] || 0;
const sum = (object) => Object.values(object).reduce((a, b) => a + b, 0);
const distance = (p) => Object.entries(p.stats.custom).reduce((a, [k, v]) => a + (k.endsWith("_one_cm") ? v : 0), 0);
const count = (n) => number.format(n);
const km = (cm) => cm >= 1e8 ? count(Math.round(cm / 1e5)) + " km" : cm >= 1e5 ? (cm / 1e5).toFixed(1) + " km" : Math.round(cm / 100) + " m";
const hearts = (tenths) => count(Math.round(tenths / 20)) + " hearts";
const calm = matchMedia("(prefers-reduced-motion: reduce)");

function duration(ticks) {
  const minutes = Math.floor(ticks / 1200);
  if (minutes < 60) return minutes + " min";
  const hours = Math.floor(minutes / 60);
  return hours < 100 ? hours + " h " + (minutes % 60) + " min" : count(hours) + " h";
}

function ago(seconds) {
  if (!seconds) return "a while ago";
  const minutes = Math.max(0, Math.floor(Date.now() / 60000 - seconds / 60));
  if (minutes < 10) return "just now";
  if (minutes < 60) return minutes + " min ago";
  if (minutes < 1440) return Math.floor(minutes / 60) + " h ago";
  const days = Math.floor(minutes / 1440);
  return days === 1 ? "yesterday" : days + " days ago";
}

// "walk_on_water" becomes "Walk on water".
function pretty(id) {
  const words = id.replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function country(code) {
  try { return regions.of(code) || code; } catch (error) { return code; }
}

function ordinal(n) {
  const tail = n % 100 >= 11 && n % 100 <= 13 ? "th" : ["th", "st", "nd", "rd"][n % 10] || "th";
  return n + tail;
}

// Makes an element with what goes inside it: other elements, or text.
function make(tag, className, ...children) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.append(...children.filter((child) => child != null && child !== false));
  return node;
}

function link(href, className, ...children) {
  const a = make("a", className, ...children);
  a.href = href;
  return a;
}

// Numbers the children, so the stylesheet can let them arrive one after another.
function staggered(box) {
  [...box.children].forEach((child, index) => child.style.setProperty("--i", index));
  box.classList.add("rise");
  return box;
}

// The same numbers every time, so the landscape does not change between visits.
function dice(seed) {
  let state = seed;
  return () => (state = (state * 1103515245 + 12345) % 2147483648) / 2147483648;
}

// A country code as its flag: a picture the script fetched, because Windows has no
// flag emoji and would show two letters. Until the picture is there, the emoji shows.
function flag(code) {
  const image = make("img", "flag");
  image.alt = image.title = "Joins from " + country(code);
  image.width = 20;
  image.onerror = () => {
    const span = make("span", "flag", String.fromCodePoint(...[...code].map((c) => 0x1F1E6 + c.charCodeAt(0) - 65)));
    span.title = image.title;
    span.setAttribute("role", "img");
    span.setAttribute("aria-label", image.title);
    image.replaceWith(span);
  };
  image.src = "/data/flags/" + code.toLowerCase() + ".png";
  return image;
}

// Skins, loaded once each: the picture, null if there is none, or a promise while
// it loads. A skin that is already here is drawn at once, so a page is complete
// the moment it is shown.
const skins = new Map();
function skin(p, draw) {
  if (!p.skin) return;
  let known = skins.get(p.uuid);
  if (known instanceof HTMLImageElement) return draw(known);
  if (known === null) return;
  if (!known) {
    known = new Promise((resolve) => {
      const image = new Image();
      image.onload = () => { skins.set(p.uuid, image); resolve(image); };
      image.onerror = () => { skins.set(p.uuid, null); resolve(null); };
      image.src = "/data/skins/" + encodeURIComponent(p.uuid) + ".png";
    });
    skins.set(p.uuid, known);
  }
  known.then((image) => image && draw(image));
}

// The skin of someone who has none, such as a Bedrock player: plain clothes,
// painted where a real skin has its head, body, arms and legs.
let plainSkin = null;
function plain() {
  if (plainSkin) return plainSkin;
  plainSkin = make("canvas");
  plainSkin.width = plainSkin.height = 64;
  const context = plainSkin.getContext("2d");
  const fill = (color, x, y, w, h) => { context.fillStyle = color; context.fillRect(x, y, w, h); };
  fill("#c9a27e", 0, 0, 32, 16);
  fill("#6b4a2f", 8, 0, 8, 8);
  fill("#6b4a2f", 0, 8, 32, 2);
  fill("#3b2a1a", 10, 12, 1, 1);
  fill("#3b2a1a", 13, 12, 1, 1);
  fill("#6f9f58", 16, 16, 24, 16);
  fill("#6f9f58", 40, 16, 16, 16);
  fill("#6f9f58", 32, 48, 16, 16);
  fill("#c9a27e", 40, 28, 16, 4);
  fill("#c9a27e", 32, 60, 16, 4);
  fill("#c9a27e", 48, 16, 4, 4);
  fill("#c9a27e", 40, 48, 4, 4);
  fill("#5b6b8a", 0, 16, 16, 16);
  fill("#5b6b8a", 16, 48, 16, 16);
  fill("#3d3a36", 0, 28, 16, 4);
  fill("#3d3a36", 16, 60, 16, 4);
  return plainSkin;
}

// Figures. A member is drawn in three dimensions from their skin, the way the
// game builds a player: six boxes, each with its six sides cut from the skin,
// and a second, slightly bigger layer for hats, jackets and sleeves. Lengths are
// in skin pixels: a figure is 32 tall and stands on the origin, facing forward.

// Turns a point about the z axis, then x, then y. Angles are in degrees.
function turn([x, y, z], [ax, ay, az]) {
  const k = Math.PI / 180;
  let c = Math.cos(az * k), s = Math.sin(az * k);
  [x, y] = [x * c - y * s, x * s + y * c];
  c = Math.cos(ax * k); s = Math.sin(ax * k);
  [y, z] = [y * c - z * s, y * s + z * c];
  c = Math.cos(ay * k); s = Math.sin(ay * k);
  [x, z] = [x * c + z * s, -x * s + z * c];
  return [x, y, z];
}

// The six sides of a box: its corners in the order the skin has them (top left,
// top right, bottom right, bottom left), which way it faces, and where its
// picture is in the skin, counted from the part's own corner, for a box w wide,
// h tall and d deep. Front, back, their right, their left, top, bottom.
const SIDES = [
  { corners: [[-1, 1, 1], [1, 1, 1], [1, -1, 1], [-1, -1, 1]], faces: [0, 0, 1], cut: (w, h, d) => [d, d, w, h] },
  { corners: [[1, 1, -1], [-1, 1, -1], [-1, -1, -1], [1, -1, -1]], faces: [0, 0, -1], cut: (w, h, d) => [2 * d + w, d, w, h] },
  { corners: [[-1, 1, -1], [-1, 1, 1], [-1, -1, 1], [-1, -1, -1]], faces: [-1, 0, 0], cut: (w, h, d) => [0, d, d, h] },
  { corners: [[1, 1, 1], [1, 1, -1], [1, -1, -1], [1, -1, 1]], faces: [1, 0, 0], cut: (w, h, d) => [d + w, d, d, h] },
  { corners: [[-1, 1, -1], [1, 1, -1], [1, 1, 1], [-1, 1, 1]], faces: [0, 1, 0], cut: (w, h, d) => [d, 0, w, d] },
  { corners: [[-1, -1, 1], [1, -1, 1], [1, -1, -1], [-1, -1, -1]], faces: [0, -1, 0], cut: (w, h, d) => [d + w, 0, w, d] },
];

// The parts of a figure: size, where the middle is, the point it turns about,
// where its picture starts in the skin, the same for its second layer, and how
// much bigger that layer is. A slim skin has narrower arms. An old skin is half
// as tall and has one arm and one leg, used mirrored for the other side, and no
// second layer but the hat.
function parts(look) {
  const arm = look.slim ? 3 : 4;
  const out = look.slim ? 5.5 : 6;
  const old = look.image.height < 64;
  const over = (x, y) => (old ? null : [x, y]);
  return [
    { name: "head", size: [8, 8, 8], at: [0, 28, 0], pivot: [0, 24, 0], skin: [0, 0], over: [32, 0], grow: 0.5 },
    { name: "body", size: [8, 12, 4], at: [0, 18, 0], pivot: [0, 24, 0], skin: [16, 16], over: over(16, 32), grow: 0.25 },
    { name: "armR", size: [arm, 12, 4], at: [-out, 18, 0], pivot: [-5, 22, 0], skin: [40, 16], over: over(40, 32), grow: 0.25 },
    { name: "armL", size: [arm, 12, 4], at: [out, 18, 0], pivot: [5, 22, 0], skin: old ? [40, 16] : [32, 48], over: over(48, 48), grow: 0.25, mirrored: old },
    { name: "legR", size: [4, 12, 4], at: [-2, 6, 0], pivot: [-2, 12, 0], skin: [0, 16], over: over(0, 32), grow: 0.25 },
    { name: "legL", size: [4, 12, 4], at: [2, 6, 0], pivot: [2, 12, 0], skin: old ? [0, 16] : [16, 48], over: over(0, 48), grow: 0.25, mirrored: old },
  ];
}

// What a picture of a figure shows, in skin pixels: the middle, and the width and
// height around it.
const FRAMES = {
  body: { x: 0, y: 18, w: 26, h: 38 },
  head: { x: 0, y: 28.3, w: 13, h: 13, only: "head" },
};
// The light comes from the upper right, where the sun is.
const LIGHT = [0.4, 0.6, 0.7];
const AWAY = 90;

// Every corner of a posed figure as the camera sees it, six numbers for each:
// where it is, which pixel of the skin it shows, how bright it is, and whether it
// belongs to the second layer, whose empty pixels are see-through.
function mesh(look, pose, frame) {
  const data = [];
  const high = look.image.height;
  for (const part of parts(look)) {
    if (frame.only && part.name !== frame.only) continue;
    const angles = pose[part.name] || [0, 0, 0];
    const [w, h, d] = part.size;
    const side = part.mirrored ? -1 : 1;
    for (const layer of [0, 1]) {
      const start = layer ? part.over : part.skin;
      if (!start) continue;
      const grow = layer ? part.grow : 0;
      for (const face of SIDES) {
        const points = face.corners.map(([x, y, z]) => {
          let point = [
            side * x * (w / 2 + grow) + part.at[0] - part.pivot[0],
            y * (h / 2 + grow) + part.at[1] - part.pivot[1],
            z * (d / 2 + grow) + part.at[2] - part.pivot[2],
          ];
          point = turn(point, angles).map((value, axis) => value + part.pivot[axis]);
          point = turn(point, [pose.pitch, pose.yaw, 0]);
          // The camera stands AWAY in front, and sees 40 either side of that sharply.
          const depth = point[2] - AWAY;
          return [
            (point[0] - frame.x) * 2 * AWAY / frame.w, (point[1] - frame.y) * 2 * AWAY / frame.h,
            -(AWAY * depth + AWAY * AWAY - 1600) / 40, -depth,
          ];
        });
        const facing = turn(turn([side * face.faces[0], face.faces[1], face.faces[2]], angles), [pose.pitch, pose.yaw, 0]);
        const bright = 0.58 + 0.5 * Math.max(0, facing[0] * LIGHT[0] + facing[1] * LIGHT[1] + facing[2] * LIGHT[2]);
        // A hair inside the edges, so a side never shows its neighbour's pixels.
        const [x, y, wide, tall] = face.cut(w, h, d);
        const left = (start[0] + x + 0.02) / 64, right = (start[0] + x + wide - 0.02) / 64;
        const top = (start[1] + y + 0.02) / high, bottom = (start[1] + y + tall - 0.02) / high;
        const corners = [[left, top], [right, top], [right, bottom], [left, bottom]];
        for (const index of [0, 1, 2, 0, 2, 3]) data.push(...points[index], ...corners[index], bright, layer);
      }
    }
  }
  return new Float32Array(data);
}

// One hidden canvas does all the drawing in three dimensions, and each figure
// copies its picture from it. Browsers allow only a few such canvases at once.
let studio = null;
function camera() {
  if (studio && !studio.gl.isContextLost()) return studio;
  if (studio === false) return null;
  const canvas = make("canvas");
  const gl = canvas.getContext("webgl", { antialias: true });
  if (!gl) { studio = false; return null; }
  const shader = (type, source) => {
    const made = gl.createShader(type);
    gl.shaderSource(made, source);
    gl.compileShader(made);
    return made;
  };
  const program = gl.createProgram();
  gl.attachShader(program, shader(gl.VERTEX_SHADER,
    "attribute vec4 place; attribute vec4 paint; varying vec4 colour; void main() { gl_Position = place; colour = paint; }"));
  gl.attachShader(program, shader(gl.FRAGMENT_SHADER,
    "precision mediump float; uniform sampler2D skin; varying vec4 colour; void main() {" +
    " vec4 pixel = texture2D(skin, colour.xy); if (colour.w > 0.5 && pixel.a < 0.4) discard;" +
    " gl_FragColor = vec4(pixel.rgb * colour.z, 1.0); }"));
  gl.linkProgram(program);
  gl.useProgram(program);
  gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
  const place = gl.getAttribLocation(program, "place");
  const paint = gl.getAttribLocation(program, "paint");
  gl.enableVertexAttribArray(place);
  gl.enableVertexAttribArray(paint);
  gl.vertexAttribPointer(place, 4, gl.FLOAT, false, 32, 0);
  gl.vertexAttribPointer(paint, 4, gl.FLOAT, false, 32, 16);
  gl.enable(gl.DEPTH_TEST);
  gl.clearColor(0, 0, 0, 0);
  studio = { canvas, gl, skins: new Map() };
  return studio;
}

// Draws a posed figure on a canvas.
function shoot(canvas, look, pose, frame) {
  const room = camera();
  if (!room) return flat(canvas, look, frame);
  const { gl } = room;
  if (room.canvas.width < canvas.width || room.canvas.height < canvas.height) {
    room.canvas.width = Math.max(room.canvas.width, canvas.width);
    room.canvas.height = Math.max(room.canvas.height, canvas.height);
  }
  let texture = room.skins.get(look.image);
  if (!texture) {
    texture = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, texture);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, look.image);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    room.skins.set(look.image, texture);
  }
  gl.bindTexture(gl.TEXTURE_2D, texture);
  gl.viewport(0, 0, canvas.width, canvas.height);
  gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
  const data = mesh(look, pose, frame);
  gl.bufferData(gl.ARRAY_BUFFER, data, gl.DYNAMIC_DRAW);
  gl.drawArrays(gl.TRIANGLES, 0, data.length / 8);
  const context = canvas.getContext("2d");
  context.clearRect(0, 0, canvas.width, canvas.height);
  context.drawImage(room.canvas, 0, room.canvas.height - canvas.height, canvas.width, canvas.height, 0, 0, canvas.width, canvas.height);
}

// The same figure from the front and flat, for a browser that cannot draw in
// three dimensions.
function flat(canvas, look, frame) {
  const context = canvas.getContext("2d");
  const scale = canvas.height / frame.h;
  context.setTransform(1, 0, 0, 1, 0, 0);
  context.clearRect(0, 0, canvas.width, canvas.height);
  context.imageSmoothingEnabled = false;
  context.setTransform(scale, 0, 0, scale, canvas.width / 2 - frame.x * scale, (frame.y + frame.h / 2 - 32) * scale);
  const image = look.image;
  const part = (sx, sy, w, h, x, y, mirrored) => {
    context.save();
    context.translate(mirrored ? x + w : x, y);
    if (mirrored) context.scale(-1, 1);
    context.drawImage(image, sx, sy, w, h, 0, 0, w, h);
    context.restore();
  };
  part(8, 8, 8, 8, -4, 0);
  if (!frame.only) {
    const arm = look.slim ? 3 : 4;
    part(20, 20, 8, 12, -4, 8);
    part(44, 20, arm, 12, -4 - arm, 8);
    part(4, 20, 4, 12, -4, 20);
    if (image.height < 64) {
      part(44, 20, arm, 12, 4, 8, true);
      part(4, 20, 4, 12, 0, 20, true);
    } else {
      part(36, 52, arm, 12, 4, 8);
      part(20, 52, 4, 12, 0, 20);
      part(20, 36, 8, 12, -4, 8);
      part(44, 36, arm, 12, -4 - arm, 8);
      part(52, 52, arm, 12, 4, 8);
      part(4, 36, 4, 12, -4, 20);
      part(4, 52, 4, 12, 0, 20);
    }
  }
  part(40, 8, 8, 8, -4, 0);
  context.setTransform(1, 0, 0, 1, 0, 0);
}

// How a figure stands. It turns a little to one side with its head towards the
// viewer, and can lift an arm to wave, by how much from 0 to 1. Time, in seconds,
// moves the waving arm and, unless the figure keeps still, makes it breathe and
// look about.
function stand(yaw, wave, time, sway, still) {
  const lift = (wave || 0) * (148 + 13 * Math.sin((time || 0) * 9));
  const t = still ? 0 : time || 0;
  return {
    yaw: yaw + 3 * Math.sin(t * 0.45), pitch: 7,
    head: [2 + 3 * Math.sin(t * 0.7), -yaw * 0.55 + 9 * Math.sin(t * 0.33), -3 * (wave || 0)],
    armR: [(sway || 0) + 2.5 * Math.sin(t * 1.5), 0, -4 - 1.5 * Math.sin(t * 1.9) - lift],
    armL: [-(sway || 0) - 2.5 * Math.sin(t * 1.5), 0, 4 + 1.5 * Math.sin(t * 1.9)],
    legR: [-(sway || 0) * 0.6, 0, 0], legL: [(sway || 0) * 0.6, 0, 0],
  };
}

// A member as a picture: all of them, or the head alone. "tall" is how high it is
// shown, so it is drawn with as many pixels as the screen has. Without a skin it
// is the plain figure, until theirs has loaded.
function figure(p, kind, tall, pose) {
  const frame = FRAMES[kind];
  const canvas = make("canvas", kind);
  const sharp = Math.min(3, window.devicePixelRatio || 1);
  canvas.height = Math.round(tall * sharp);
  canvas.width = Math.round(tall * sharp * frame.w / frame.h);
  canvas.setAttribute("aria-hidden", "true");
  canvas.pose = pose || (kind === "head" ? { yaw: -26, pitch: 0, head: [12, 0, 0] } : stand(-16));
  const draw = (image) => {
    canvas.look = { image, slim: p.skin === "slim" };
    shoot(canvas, canvas.look, typeof canvas.pose === "function" ? canvas.pose(0) : canvas.pose, frame);
  };
  if (!(skins.get(p.uuid) instanceof HTMLImageElement)) draw(plain());
  skin(p, draw);
  return canvas;
}

// Figures that move are drawn again for every frame, for as long as they are on
// the page. Nothing moves for someone who has asked for less motion.
const moving = new Set();
function animate(canvas, pose) {
  canvas.pose = pose;
  if (calm.matches) return;
  if (!moving.size) requestAnimationFrame(frames);
  moving.add(canvas);
}
function frames(now) {
  for (const canvas of moving) {
    if (!canvas.isConnected) moving.delete(canvas);
    else if (!document.hidden) shoot(canvas, canvas.look, canvas.pose(now / 1000), FRAMES.body);
  }
  if (moving.size) requestAnimationFrame(frames);
}

const TRAVEL = {
  walk: "Walked", sprint: "Sprinted", crouch: "Crouched", swim: "Swum", fly: "Flown",
  aviate: "By elytra", boat: "By boat", horse: "By horse", minecart: "By minecart",
  pig: "By pig", strider: "By strider", climb: "Climbed", fall: "Fallen",
  walk_on_water: "Walked on water", walk_under_water: "Walked under water",
};

const COLUMNS = [
  { label: "Member", get: (p) => p.name.toLowerCase(), text: true },
  { label: "Play time", tile: "Time played", get: (p) => custom(p, "play_time"), show: duration },
  { label: "Last played", get: (p) => p.seen || 0, show: ago },
  { label: "Deaths", tile: "Deaths", get: (p) => custom(p, "deaths") },
  { label: "Mob kills", tile: "Mobs killed", get: (p) => custom(p, "mob_kills") },
  { label: "Blocks mined", tile: "Blocks mined", get: (p) => sum(p.stats.mined) },
  { label: "Travelled", tile: "Travelled", get: distance, show: km },
  { label: "Items crafted", tile: "Items crafted", get: (p) => sum(p.stats.crafted) },
  { label: "Trades", tile: "Villager trades", get: (p) => custom(p, "traded_with_villager") },
];
const shown = (column, p) => (column.show ? column.show(column.get(p)) : count(column.get(p)));

const RECORDS = [
  { label: "Homebody", what: "played", column: 1 },
  { label: "Deepest digger", what: "blocks mined", column: 5 },
  { label: "Monster hunter", what: "mobs killed", column: 4 },
  { label: "Wanderer", what: "travelled", column: 6 },
  { label: "Best customer", what: "trades with villagers", column: 8 },
  { label: "Most adventurous", what: "deaths", column: 3 },
];

let players = [];
// "waiting" until the numbers have come once, then "ready", or "empty" if there are none.
let state = "waiting";
// When the numbers were written. A file saved within a few minutes of that belongs
// to someone who is playing, because the game saves everyone who is online together.
let updated = 0;
let sort = { column: 1, down: true };
const playing = (p) => p.seen && updated - p.seen < 7 * 60;
const home = (p) => "/members/" + encodeURIComponent(p.name);
const status = (p) => (playing(p) ? "Playing now" : "Last played " + ago(p.seen));
const dot = () => make("i", "live");

// The feats, which the ledger keeps and the script fetches (see README): which
// there are, and what each member holds, by their id. Both stay empty until they
// have come, and when there are none, and then the hub shows no feats.
let feats = [];
let held = new Map();
const byId = (uuid) => players.find((p) => p.uuid === uuid);

// What a member holds, the newest first, one line for each feat. A feat that can
// be earned again counts how often, and shows the latest time.
function holds(p) {
  const lines = new Map();
  for (const one of held.get(p.uuid) || []) {
    const info = feats.find((feat) => feat.key === one.feat);
    if (!info) continue;
    const line = lines.get(info.key);
    if (!line) lines.set(info.key, { info, times: 1, at: one.at, among: one.with || [], number: one.number });
    else {
      line.times += 1;
      if (one.at > line.at) Object.assign(line, { at: one.at, among: one.with || [], number: one.number });
    }
  }
  return [...lines.values()].sort((a, b) => b.at - a.at);
}

// Everything anyone has earned, the newest first.
function earnings() {
  const all = [];
  for (const p of players) {
    for (const one of held.get(p.uuid) || []) {
      const info = feats.find((feat) => feat.key === one.feat);
      if (info) all.push({ p, info, at: one.at });
    }
  }
  return all.sort((a, b) => b.at - a.at);
}

// When something was earned: how long ago in the first week, and after that the day.
function when(seconds) {
  if (Date.now() / 1000 - seconds < 7 * 86400) return ago(seconds);
  return new Date(seconds * 1000).toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" });
}

// The picture that stands for feats, as the row of pages has it.
function medal() {
  return document.querySelector('#nav a[href="/feats"] svg').cloneNode(true);
}

// The faces of the members something was earned with, each a way to their page.
function company(uuids) {
  const others = uuids.map(byId).filter(Boolean);
  if (!others.length) return null;
  const row = make("span", "company", make("small", null, "With"));
  for (const other of others) {
    const a = link(home(other), null, figure(other, "head", 28));
    a.title = other.name;
    a.setAttribute("aria-label", other.name);
    row.append(a);
  }
  return row;
}

// The number that goes with a feat, in words, for the feats that have one.
function told(line) {
  if (line.number == null) return null;
  if (line.info.key === "day-to-remember") return "Passed their own best of " + count(line.number) + " blocks in a day";
  if (line.info.key === "first-million") return "Block number " + count(line.number);
  return null;
}

// Each record with the member who holds it, leaving out records nobody has a number for.
function holders() {
  return RECORDS.map((record) => {
    const column = COLUMNS[record.column];
    const best = players.reduce((a, b) => (column.get(b) > column.get(a) ? b : a), players[0]);
    return { ...record, best, text: shown(column, best) + " " + record.what, any: column.get(best) > 0 };
  }).filter((record) => record.any);
}

// The landscape. Every page opens with a view over the acres: a sky, hills that
// fade into the distance, and the meadow in front, all made of blocks like the
// world itself and drawn here, so there are no picture files.

// Wide enough for any screen. What does not fit is cut off at the edges.
const WIDE = Math.max(2600, screen.width || 0);

function drawing(className, height, wide) {
  const node = document.createElementNS(NS, "svg");
  node.setAttribute("class", className);
  node.setAttribute("width", wide || WIDE);
  node.setAttribute("height", height);
  node.setAttribute("aria-hidden", "true");
  return node;
}

function block(parent, x, y, width, height, fill, className) {
  const node = document.createElementNS(NS, "rect");
  for (const [name, value] of Object.entries({ x, y, width, height })) node.setAttribute(name, value);
  node.style.fill = fill;
  if (className) node.setAttribute("class", className);
  parent.append(node);
  return node;
}

// A tree, standing on (x, y), with blocks u wide. Far away it is one colour.
function tree(parent, x, y, u, pine, haze) {
  const leaf = haze || "var(--leaf)";
  if (pine) {
    block(parent, x - u / 2, y - 2 * u, u, 2 * u, haze || "var(--trunk)");
    [5, 3, 3, 1, 1].forEach((wide, row) => block(parent, x - wide * u / 2, y - (3 + row) * u, wide * u, u, haze || "var(--pine)"));
    if (!haze) [[0.5, 3, 2], [0.5, 5, 1]].forEach(([dx, row, wide]) => block(parent, x + (dx - 0.5) * u, y - row * u, wide * u, u, leaf));
    return;
  }
  block(parent, x - u / 2, y - 3 * u, u, 3 * u, haze || "var(--trunk)");
  block(parent, x - 2.5 * u, y - 5 * u, 5 * u, 2 * u, leaf);
  block(parent, x - 1.5 * u, y - 7 * u, 3 * u, 2 * u, leaf);
  if (haze) return;
  block(parent, x + 0.5 * u, y - 5 * u, 2 * u, u, "var(--leaf-lit)");
  block(parent, x - 0.5 * u, y - 7 * u, 2 * u, u, "var(--leaf-lit)");
  block(parent, x - 2.5 * u, y - 4 * u, 2 * u, u, "var(--leaf-dim)");
  block(parent, x - 1.5 * u, y - 6 * u, u, u, "var(--leaf-dim)");
}

// A cottage with smoke from its chimney, and windows that glow at night.
function cottage(parent, x, y, u) {
  for (const delay of [0, 2, 4]) block(parent, x + 2 * u, y - 9.5 * u, u, u, "var(--smoke)", "puff").style.animationDelay = delay + "s";
  block(parent, x + 2 * u, y - 8.5 * u, u, 3 * u, "var(--chimney)");
  block(parent, x - 3.5 * u, y - 4 * u, 7 * u, 4 * u, "var(--wall)");
  [9, 7, 5, 3].forEach((wide, row) => block(parent, x - wide * u / 2, y - (5 + row) * u, wide * u, u, "var(--roof)"));
  block(parent, x - 0.5 * u, y - 2 * u, u, 2 * u, "var(--door)");
  block(parent, x - 2.5 * u, y - 3 * u, u, u, "var(--window)", "window");
  block(parent, x + 1.5 * u, y - 3 * u, u, u, "var(--window)", "window");
}

// One line of hills: columns of blocks whose tops follow a slow wave, lighter
// along the top where the light falls, with trees standing on them. "from" and
// "to" are the colours at the top and at the foot.
let gradients = 0;
function ridge(node, height, o) {
  const roll = dice(o.seed);
  const tops = [];
  for (let x = 0; x < (o.wide || WIDE); x += o.size) {
    const wave = Math.sin(x * o.pace + o.shift) + 0.45 * Math.sin(x * o.pace * 2.7 + o.shift * 3) + 0.2 * Math.sin(x * o.pace * 6.3 + o.shift * 7);
    tops.push(height - Math.max(1, Math.round((o.base + o.swing * wave) / o.size)) * o.size);
  }
  let since = 99;
  tops.forEach((top, index) => {
    since += 1;
    const x = (index + 0.5) * o.size;
    if (o.cottage && Math.abs(x - o.cottage) < o.size / 2) {
      cottage(node, x, top, o.size * 0.75);
      since = -6;
    } else if (o.trees && since > 4 && roll() < o.trees) {
      tree(node, x, top, o.unit * (0.85 + 0.4 * roll()), roll() < 0.3, o.haze);
      since = 0;
    }
  });
  const d = `M0 ${height}` + tops.map((top, index) => `V${top}H${(index + 1) * o.size}`).join("") + `V${height}Z`;
  const id = "fade" + (gradients += 1);
  const gradient = document.createElementNS(NS, "linearGradient");
  gradient.id = id;
  gradient.setAttribute("gradientUnits", "userSpaceOnUse");
  for (const [name, value] of Object.entries({ x1: 0, x2: 0, y1: Math.min(...tops), y2: height })) gradient.setAttribute(name, value);
  [o.from, o.to].forEach((colour, index) => {
    const stop = document.createElementNS(NS, "stop");
    stop.setAttribute("offset", index);
    stop.style.stopColor = colour;
    gradient.append(stop);
  });
  const cap = document.createElementNS(NS, "path");
  cap.setAttribute("d", d);
  cap.style.fill = o.cap;
  const body = document.createElementNS(NS, "path");
  body.setAttribute("d", d);
  body.setAttribute("transform", `translate(0 ${o.lit || 3})`);
  body.setAttribute("fill", `url(#${id})`);
  node.append(gradient, cap, body);
}

// The hills in the distance: three lines of them, each nearer, darker and with
// bigger blocks than the one behind. The cottage stands where a phone shows it too.
function hills(height, seed) {
  const node = drawing("far", height);
  const roll = dice(seed);
  const shift = roll() * 30;
  const place = Math.min(window.innerWidth, 1400) * (0.2 + 0.6 * roll());
  ridge(node, height, { seed, size: 4, base: height * 0.62, swing: height * 0.13, pace: 0.0055, shift,
    from: "var(--far-1)", to: "var(--far-1-low)", cap: "var(--far-1-cap)", lit: 2 });
  ridge(node, height, { seed: seed + 1, size: 6, base: height * 0.41, swing: height * 0.11, pace: 0.0085, shift: shift + 2,
    from: "var(--far-2)", to: "var(--far-2-low)", cap: "var(--far-2-cap)", lit: 2, trees: 0.12, unit: 4, haze: "var(--far-2-tree)" });
  ridge(node, height, { seed: seed + 2, size: 8, base: height * 0.2, swing: height * 0.07, pace: 0.012, shift: shift + 5,
    from: "var(--far-3)", to: "var(--far-3-low)", cap: "var(--far-3-cap)", trees: 0.14, unit: 6, cottage: Math.round(place / 8) * 8 + 4 });
  return node;
}

// A stretch of meadow in front of the hills, with a rolling top edge. Whatever is
// passed in stands on it. The last one fades into the page below.
function acre(order, seed, last, ...children) {
  const crest = drawing("crest", 64);
  ridge(crest, 64, { seed, size: 8 + 2 * order, base: 22, swing: 9, pace: 0.019 - order * 0.003, shift: seed,
    from: "var(--acre)", to: "var(--acre)", cap: "var(--acre-cap)", lit: 4, trees: 0.045, unit: 7 + order });
  const box = make("div", `acre a${order}` + (last ? " last" : ""), crest, make("i", "flora"), ...children);
  return box;
}

// A small view of the acres for one member to stand in, on a panel: a hill behind
// and a rise in front, different for each.
function plot(seed, body) {
  const wide = 440;
  const land = drawing("patch", 72, wide);
  ridge(land, 72, { wide, seed, size: 4, base: 52, swing: 8, pace: 0.021, shift: seed * 1.7,
    from: "var(--far-2)", to: "var(--far-2-low)", cap: "var(--far-2-cap)", lit: 2, trees: 0.09, unit: 3, haze: "var(--far-2-tree)" });
  ridge(land, 72, { wide, seed: seed + 3, size: 6, base: 31, swing: 4, pace: 0.034, shift: seed * 2.3,
    from: "var(--acre-1)", to: "var(--acre-2)", cap: "var(--acre-cap)" });
  return make("span", "plot", land, body, make("i", "shade"));
}

// What grows in the meadow: lighter and darker patches, tufts of grass and a few
// flowers, as one picture that repeats.
function flora() {
  const roll = dice(23);
  const wide = 560, high = 280;
  let inside = "";
  const at = (step) => [Math.floor(roll() * wide / step) * step, Math.floor(roll() * high / step) * step];
  for (let i = 0; i < 110; i++) {
    const [x, y] = at(6);
    inside += `<rect x="${x}" y="${y}" width="6" height="6" fill="${i % 3 ? "#000" : "#fff"}" opacity="${i % 3 ? 0.045 : 0.06}"/>`;
  }
  for (let i = 0; i < 46; i++) {
    const [x, y] = at(2);
    inside += `<path d="M${x} ${y}h2v6h-2zM${x + 4} ${y + 2}h2v4h-2z" fill="#d8f7a8" opacity="0.4"/>`;
  }
  const petals = ["#fff", "#ffe36e", "#ffb3c7", "#fff", "#ff8d7a", "#cfe3ff"];
  for (let i = 0; i < 20; i++) {
    const [x, y] = at(2);
    inside += `<path d="M${x + 2} ${y + 6}h2v5h-2z" fill="#1f5a2b" opacity="0.6"/>`
      + `<path d="M${x + 2} ${y}h2v2h2v2h-2v2h-2v-2h-2v-2h2z" fill="${petals[i % petals.length]}"/>`
      + `<rect x="${x + 2}" y="${y + 2}" width="2" height="2" fill="#f5b82e"/>`;
  }
  const picture = `<svg xmlns="${NS}" width="${wide}" height="${high}" shape-rendering="crispEdges">${inside}</svg>`;
  document.documentElement.style.setProperty("--flora", `url("data:image/svg+xml,${encodeURIComponent(picture)}")`);
}

// The stars, as shadows of one small square: three lots that twinkle in turn.
function stars() {
  const roll = dice(5);
  for (const lot of [1, 2, 3]) {
    const shadows = [];
    for (let i = 0; i < 30; i++) {
      shadows.push(`${Math.round(roll() * WIDE)}px ${Math.round(roll() * roll() * 520)}px 0 ${roll() < 0.25 ? 1 : 0}px rgba(246, 242, 214, ${(0.5 + 0.5 * roll()).toFixed(2)})`);
    }
    document.documentElement.style.setProperty("--stars-" + lot, shadows.join(","));
  }
}
flora();
stars();

// The view a page opens with: the sky, what the page says about itself, the far
// hills, and then the meadow.
function scene(kind, seed, words, ...ground) {
  const sky = make("div", "sky",
    make("i", "glow"), make("i", "sun"), make("i", "stars s1"), make("i", "stars s2"), make("i", "stars s3"),
    make("i", "cloud c1"), make("i", "cloud c2"), make("i", "cloud c3"), make("i", "cloud c4"));
  sky.setAttribute("aria-hidden", "true");
  const box = make("section", "scene " + kind, sky, words, hills(kind === "home" && window.innerWidth >= 560 ? 170 : 120, seed), ...ground);
  // Fireflies, which only show at night.
  const roll = dice(seed + 9);
  for (let i = 0; i < 9; i++) {
    const fly = make("i", "fly");
    fly.style.left = (4 + 92 * roll()).toFixed(1) + "%";
    fly.style.bottom = (20 + 240 * roll()).toFixed(0) + "px";
    fly.style.animationDelay = (-12 * roll()).toFixed(1) + "s";
    fly.style.animationDuration = (9 + 8 * roll()).toFixed(1) + "s";
    box.append(fly);
  }
  return box;
}

// Splits a line of things, each so wide, into as few rows as fit the room, with
// the rows as even as they can be. Returns how many go in each row.
function rows(widths, room, gap) {
  const n = widths.length;
  const before = [0];
  widths.forEach((wide, index) => before.push(before[index] + wide + gap));
  const span = (from, to) => before[to] - before[from] - gap;
  for (let count = 1; count < n; count++) {
    // best[k][j] is the widest row when the first j things are in k + 1 rows.
    const best = [before.map((_, j) => (j ? span(0, j) : 0))];
    const cut = [[]];
    for (let k = 1; k < count; k++) {
      best.push([]);
      cut.push([]);
      for (let j = k + 1; j <= n; j++) {
        best[k][j] = Infinity;
        for (let i = k; i < j; i++) {
          const widest = Math.max(best[k - 1][i], span(i, j));
          if (widest < best[k][j]) { best[k][j] = widest; cut[k][j] = i; }
        }
      }
    }
    if (best[count - 1][n] <= room) {
      const sizes = [];
      for (let k = count - 1, j = n; k >= 0; k--) {
        const i = k ? cut[k][j] : 0;
        sizes.unshift(j - i);
        j = i;
      }
      return sizes;
    }
  }
  return widths.map(() => 1);
}

// Everyone together on the meadow, a row to each stretch of it, whoever has
// played most in the middle. Someone who is playing waves.
const pencil = make("canvas").getContext("2d");
let fitted = 0;
function lineup(seed) {
  const small = window.innerWidth < 560;
  const tall = small ? 124 : 148;
  const order = [];
  [...players].sort((a, b) => custom(b, "play_time") - custom(a, "play_time"))
    .forEach((p, index) => (index % 2 ? order.unshift(p) : order.push(p)));
  pencil.font = `700 ${small ? 11 : 12}px Figtree, system-ui, sans-serif`;
  const widths = order.map((p) => Math.max(small ? 62 : 84, Math.ceil(pencil.measureText(p.name).width * 1.06) + (playing(p) ? 31 : 18)));
  fitted = Math.min(window.innerWidth, 1180) - (small ? 12 : 24);
  const sizes = rows(widths, fitted, small ? 6 : 14);
  const acres = [];
  let next = 0;
  sizes.forEach((size, line) => {
    const row = make("div", "row");
    for (let i = 0; i < size; i++, next++) {
      const p = order[next];
      const side = size > 1 ? (i / (size - 1)) * 2 - 1 : 0;
      const yaw = -side * 20 + (next * 37 % 11) - 5;
      const sway = (next * 53 % 9) - 4;
      const waves = next % 5 === 3;
      const body = figure(p, "body", tall, stand(yaw, waves ? 1 : 0, 0, sway));
      if (playing(p)) animate(body, (time) => stand(yaw, 1, time + next, sway));
      const tag = make("span", "tag", playing(p) && dot(), make("b", null, p.name), p.country && flag(p.country));
      const a = link(home(p), playing(p) ? "standee playing" : "standee", body, make("i", "shade"), tag);
      a.style.width = widths[next] + "px";
      a.style.setProperty("--i", next);
      a.dataset.uuid = p.uuid;
      a.title = p.name + ": " + status(p).toLowerCase();
      // Whoever the pointer comes to waves back.
      if (!waves && !playing(p)) a.addEventListener("pointerenter", () => {
        if (moving.has(body)) return;
        let began = null;
        animate(body, (time) => {
          if (began === null) began = time;
          const t = time - began;
          if (t > 1.7) moving.delete(body);
          return stand(yaw, Math.max(0, Math.min(1, t * 6, (1.7 - t) * 5)), t, sway, true);
        });
      });
      row.append(a);
    }
    acres.push(acre(Math.min(3, line + 1 + Math.max(0, 3 - sizes.length)), seed + line * 7, line === sizes.length - 1, row));
  });
  return acres;
}

// A name that the stylesheet sets smaller the longer it is.
function called(tag, p, ...more) {
  const node = make(tag, "name", p.name, ...more);
  node.style.setProperty("--len", p.name.length);
  return node;
}

// A row in a short list of members: the face, the name, and a word about them.
function person(p, word, under) {
  const a = link(home(p), null, figure(p, "head", 40), make("span", null, make("b", null, p.name, playing(p) && dot()), under && make("small", null, under)), make("em", null, word));
  return make("li", null, a);
}

function arrow() {
  const node = make("i", "arrow");
  node.setAttribute("aria-hidden", "true");
  return node;
}

function homePage() {
  const hours = Math.round(players.reduce((a, p) => a + custom(p, "play_time"), 0) / 72000);
  const here = players.filter(playing).length;
  const words = make("div", "words",
    make("p", "over", "Welcome back to"),
    make("h1", null, "Lush Acres"),
    make("p", "tally",
      count(hours) + " hours together, " +
      count(players.reduce((a, p) => a + sum(p.stats.mined), 0)) + " blocks mined and " +
      km(players.reduce((a, p) => a + distance(p), 0)) + " wandered."),
    make("p", here ? "now on" : "now", here ? dot() : null,
      here ? (here === 1 ? "One of us is playing right now" : here + " of us are playing right now") : "The meadow is quiet right now"),
  );

  const recent = make("ul", "people");
  [...players].sort((a, b) => (b.seen || 0) - (a.seen || 0)).slice(0, 5)
    .forEach((p) => recent.append(person(p, playing(p) ? "Playing now" : ago(p.seen))));
  const around = make("section", "card", make("h2", null, "Around the acres"), recent, link("/members", "more", "Everyone", arrow()));

  const best = make("ul", "people");
  holders().slice(0, 5).forEach((record) => best.append(person(record.best, shown(COLUMNS[record.column], record.best), record.label)));
  const board = make("section", "card", make("h2", null, "On the noticeboard"), best, link("/records", "more", "All the records", arrow()));

  const earned = make("ul", "people");
  const newest = earnings().slice(0, 5);
  newest.forEach((one) => earned.append(person(one.p, ago(one.at), one.info.name)));
  const lately = newest.length
    ? make("section", "card", make("h2", null, "Lately earned"), earned, link("/feats", "more", "All the feats", arrow()))
    : null;

  const doors = make("div", "doors");
  for (const [href, name, text] of [
    ["/members", "Members", "Everyone who calls the acres home."],
    ["/records", "Records", "Who has been up to what."],
    feats.length && ["/feats", "Feats", "Earned alone and together."],
    ["/map/", "The map", "See what everyone has built."],
  ].filter(Boolean)) {
    const picture = document.querySelector(`#nav a[href="${href}"] svg`).cloneNode(true);
    doors.append(link(href, "card door", picture, make("span", null, make("strong", null, name), make("small", null, text)), arrow()));
  }

  const box = new DocumentFragment();
  box.append(scene("home", 3, words, ...lineup(40)), make("div", "page", staggered(make("div", "two", around, board, lately)), staggered(doors)));
  return box;
}

// What one member holds, as panels on their page. Nothing at all when they hold none.
function featsOf(p) {
  const lines = holds(p);
  if (!lines.length) return null;
  const grid = make("div", "feats");
  for (const line of lines) {
    grid.append(make("div", "card feat", medal(), make("span", null,
      make("strong", null, line.info.name, line.times > 1 && make("span", "chip", line.times + " times")),
      told(line) && make("em", null, told(line)),
      company(line.among),
      make("small", null, when(line.at)))));
  }
  return staggered(grid);
}

// Every feat there is: how it is earned, and who holds it, whoever earned it first in front.
function featsPage() {
  if (!feats.length) return quiet("Feats", "No feats to show right now.", link("/", "button", arrow(), "Back to the meadow"));
  const grid = make("div", "featlist");
  for (const info of feats) {
    const who = players
      .map((p) => ({ p, line: holds(p).find((line) => line.info.key === info.key) }))
      .filter((one) => one.line)
      .sort((a, b) => a.line.at - b.line.at);
    const faces = make("div", "holders");
    who.forEach((one) => faces.append(link(home(one.p), null, figure(one.p, "head", 28), make("b", null, one.p.name))));
    grid.append(make("section", "card featcard",
      make("h3", null, medal(), info.name),
      make("p", null, info.earned, info.repeatable && " It can be earned again."),
      who.length ? faces : make("p", "none", "Nobody yet.")));
  }
  const words = make("div", "words", make("h1", null, "Feats"), make("p", "tally", "Earned on the acres, alone and together."));
  const box = new DocumentFragment();
  box.append(scene("sub", 53, words, acre(3, 75, true)), make("div", "page", staggered(grid)));
  return box;
}

function membersPage() {
  const grid = make("div", "members");
  // Whoever is playing first, then whoever was here last.
  [...players].sort((a, b) => (b.seen || 0) - (a.seen || 0)).forEach((p, index) => {
    const where = make("span", playing(p) ? "status on" : "status", playing(p) && dot(), playing(p) ? "Playing now" : ago(p.seen));
    const a = link(home(p), "card member",
      plot(100 + index * 13, figure(p, "body", 132)),
      called("strong", p, p.country && flag(p.country)),
      where,
      make("small", null, duration(custom(p, "play_time")) + " played"),
      p.name.startsWith(".") && make("span", "chip", "Bedrock"));
    a.dataset.uuid = p.uuid;
    grid.append(a);
  });
  const words = make("div", "words", make("h1", null, "Members"), make("p", "tally", "Everyone who calls the acres home."));
  const box = new DocumentFragment();
  box.append(scene("sub", 11, words, acre(3, 60, true)), make("div", "page", staggered(grid)));
  return box;
}

// A plain list has no bars, for numbers that do not compare with each other.
function list(title, entries, show, bare) {
  const box = make("section", "card", make("h3", null, title));
  const top = entries.filter(([, value]) => value > 0).sort((a, b) => b[1] - a[1]).slice(0, bare ? 20 : 8);
  if (!top.length) {
    box.append(make("p", null, "Nothing yet"));
    return box;
  }
  const ol = make("ol", bare ? "bare" : null);
  for (const [name, value] of top) {
    const li = make("li", null, make("span", null, name), make("b", null, (show || count)(value)));
    if (!bare) li.style.setProperty("--share", (100 * value / top[0][1]).toFixed(1) + "%");
    ol.append(li);
  }
  box.append(ol);
  return box;
}

const named = (object) => Object.entries(object).map(([id, value]) => [pretty(id), value]);

// The member on their own page: bigger, breathing and looking about, waving when
// the page opens and when they are touched, and turning when they are dragged.
function star(p) {
  const tall = window.innerWidth < 560 ? 236 : 288;
  const canvas = figure(p, "body", tall);
  let spin = 0, speed = 0, held = null, began = null, waving = [0.5, 2.3];
  canvas.classList.add("star");
  canvas.title = "Drag to turn";
  animate(canvas, (time) => {
    if (began === null) began = time;
    const t = time - began;
    if (held === null) { spin += speed; speed *= 0.94; }
    const wave = Math.max(0, Math.min(1, (t - waving[0]) * 5, (waving[1] - t) * 4));
    return stand(-16 + spin, wave, t);
  });
  canvas.addEventListener("pointerdown", (event) => {
    held = { x: event.clientX, moved: 0 };
    canvas.setPointerCapture(event.pointerId);
  });
  canvas.addEventListener("pointermove", (event) => {
    if (!held) return;
    speed = (event.clientX - held.x) * 0.7;
    spin += speed;
    held.moved += Math.abs(event.clientX - held.x);
    held.x = event.clientX;
    // With less motion asked for nothing moves by itself, but they still turn.
    if (calm.matches) shoot(canvas, canvas.look, stand(-16 + spin), FRAMES.body);
  });
  const release = () => {
    if (held && held.moved < 6 && began !== null) {
      const t = performance.now() / 1000 - began;
      waving = [t, t + 1.8];
    }
    held = null;
  };
  canvas.addEventListener("pointerup", release);
  canvas.addEventListener("pointercancel", release);
  return canvas;
}

function memberPage(p) {
  const titles = make("div", "titles");
  holders().filter((record) => record.best === p).forEach((record) => titles.append(make("span", "title", record.label)));
  const facts = make("p", "facts",
    make("span", playing(p) ? "status on" : "status", playing(p) && dot(), status(p)),
    p.country && make("span", null, flag(p.country), "Joins from " + country(p.country)),
    p.name.startsWith(".") && make("span", null, "Plays on Bedrock"));
  const words = make("div", "words", link("/members", "back", arrow(), "Members"), called("h1", p), facts);
  const stage = make("div", "stage", star(p), make("i", "shade"), titles.children.length ? titles : null);

  const tiles = make("div", "tiles");
  for (const column of COLUMNS.filter((c) => c.tile)) {
    const place = 1 + players.filter((other) => column.get(other) > column.get(p)).length;
    const tile = make("div", "card tile", make("small", null, column.tile), make("strong", null, shown(column, p)));
    if (column.get(p) > 0) tile.append(make("em", place === 1 ? "first" : null, place === 1 ? "Most of anyone" : ordinal(place) + " most"));
    tiles.append(tile);
  }

  const travel = Object.entries(p.stats.custom)
    .filter(([key]) => key.endsWith("_one_cm"))
    .map(([key, value]) => { const id = key.slice(0, -7); return [TRAVEL[id] || pretty(id), value]; });
  const other = [
    ["Jumps", custom(p, "jump")],
    ["Player kills", custom(p, "player_kills")],
    ["Nights slept", custom(p, "sleep_in_bed")],
    ["Animals bred", custom(p, "animals_bred")],
    ["Fish caught", custom(p, "fish_caught")],
    ["Items enchanted", custom(p, "enchant_item")],
    ["Raids won", custom(p, "raid_win")],
    ["Chests opened", custom(p, "open_chest")],
    ["Tools broken", sum(p.stats.broken)],
  ];
  const health = [
    ["Damage dealt", custom(p, "damage_dealt")],
    ["Damage taken", custom(p, "damage_taken")],
    ["Blocked by shield", custom(p, "damage_blocked_by_shield")],
  ];
  const time = [
    ["Since last death", custom(p, "time_since_death")],
    ["Crouching", custom(p, "sneak_time")],
  ];
  const lists = make("div", "lists",
    list("Most mined", named(p.stats.mined)),
    list("Most crafted", named(p.stats.crafted)),
    list("Most used", named(p.stats.used)),
    list("Mobs killed", named(p.stats.killed)),
    list("Killed by", named(p.stats.killed_by)),
    list("Travel", travel, km),
    list("Other", other, count, true),
    list("Health", health, hearts, true),
    list("Time", time, duration, true),
  );

  // On to the members either side, in the order the members page has them.
  const everyone = [...players].sort((a, b) => (b.seen || 0) - (a.seen || 0));
  const index = everyone.indexOf(p);
  const beside = make("nav", "beside");
  beside.setAttribute("aria-label", "Other members");
  for (const [other, way] of [[everyone[index - 1], "before"], [everyone[index + 1], "after"]]) {
    if (other) beside.append(link(home(other), "card " + way, arrow(), figure(other, "head", 40), make("b", null, other.name)));
  }

  const box = new DocumentFragment();
  box.append(scene("sub solo", 20 + index, words, acre(3, 80 + index, true, stage)), make("div", "page", staggered(tiles), featsOf(p), staggered(lists), beside));
  return box;
}

// Everyone's numbers, twice: on a narrow screen one number at a time, chosen from
// a row of buttons, and on a wide one a table that sorts by any column. Choosing
// draws only this again.
function numbers(box) {
  const column = COLUMNS[sort.column];
  const sorted = [...players].sort((a, b) => {
    const x = column.get(a), y = column.get(b);
    const order = x < y ? -1 : x > y ? 1 : a.name.localeCompare(b.name);
    return sort.down ? -order : order;
  });

  const choices = make("div", "choices");
  COLUMNS.forEach((c, index) => {
    if (!c.tile) return;
    const button = make("button", null, c.label);
    button.type = "button";
    button.setAttribute("aria-pressed", index === sort.column);
    button.addEventListener("click", () => { sort = { column: index, down: true }; numbers(box); });
    choices.append(button);
  });
  const most = Math.max(1, ...players.map((p) => (column.tile ? column.get(p) : 0)));
  const ranks = make("ol", "ranks");
  sorted.forEach((p, index) => {
    const a = link(home(p), null, make("i", "rank", String(index + 1)), figure(p, "head", 40), make("b", null, p.name), p.country && flag(p.country), make("em", null, shown(column, p)));
    if (column.tile) a.style.setProperty("--share", (100 * column.get(p) / most).toFixed(1) + "%");
    ranks.append(make("li", null, a));
  });

  const head = make("tr", null, make("th"));
  COLUMNS.forEach((c, index) => {
    const button = make("button", null, c.label);
    button.type = "button";
    button.addEventListener("click", () => {
      sort = { column: index, down: index === sort.column ? !sort.down : !c.text };
      numbers(box);
    });
    const th = make("th", null, button);
    th.scope = "col";
    if (index === sort.column) th.setAttribute("aria-sort", sort.down ? "descending" : "ascending");
    head.append(th);
  });
  const body = make("tbody");
  sorted.forEach((p, index) => {
    const tr = make("tr", null, make("td", "rank", String(index + 1)));
    COLUMNS.forEach((c, i) => {
      if (i > 0) return tr.append(make("td", i === sort.column ? "chosen" : null, shown(c, p)));
      const a = link(home(p), null, figure(p, "head", 40), make("b", null, p.name), playing(p) && dot(), p.country && flag(p.country), p.name.startsWith(".") && make("span", "chip", "Bedrock"));
      tr.append(make("td", "name", a));
    });
    body.append(tr);
  });
  box.replaceChildren(
    make("div", "narrow", choices, make("div", "card", ranks)),
    make("div", "card wide", make("table", null, make("thead", null, head), body)));
}

function heading(title, small) {
  return make("h2", null, title, make("small", null, small));
}

function recordsPage() {
  const records = make("div", "records");
  holders().forEach((record, index) => {
    const yaw = index % 2 ? 14 : -14;
    const a = link(home(record.best), "card record",
      plot(300 + index * 17, figure(record.best, "body", 132, stand(yaw, 1, 0.1 + index))),
      make("span", "title", record.label), called("strong", record.best), make("em", null, record.text));
    a.dataset.uuid = record.best.uuid;
    records.append(a);
  });
  const all = make("div", "numbers");
  numbers(all);
  const words = make("div", "words", make("h1", null, "Records"), make("p", "tally", "Who has been up to what."));
  const box = new DocumentFragment();
  box.append(scene("sub", 31, words, acre(3, 70, true)), make("div", "page",
    heading("The noticeboard", "Titles for the taking."), staggered(records),
    heading("Everyone's numbers", "Pick a number to rank by it, or a member to visit them."), all));
  return box;
}

// A page with nothing on it but a few words: an address that names no page, and
// the moments before the numbers have come.
function quiet(title, text, way) {
  const words = make("div", "words", make("h1", null, title), text && make("p", "tally", text), way);
  const box = new DocumentFragment();
  box.append(scene("sub", 47, words, acre(3, 90, true)), make("div", "page"));
  return box;
}

const lostPage = () => quiet("Nothing here", "This path leads nowhere.", link("/", "button", arrow(), "Back to the meadow"));

// Which page an address names.
function route(path) {
  let parts;
  try { parts = path.split("/").filter(Boolean).map(decodeURIComponent); } catch (error) { return { page: "lost" }; }
  if (!parts.length) return { page: "home" };
  if (parts[0] === "members" && parts.length === 1) return { page: "members" };
  if (parts[0] === "members" && parts.length === 2) {
    const wanted = parts[1].toLowerCase();
    const member = players.find((p) => p.name.toLowerCase() === wanted || p.uuid === wanted);
    return member ? { page: "members", member } : { page: "lost" };
  }
  if (parts[0] === "records" && parts.length === 1) return { page: "records" };
  if (parts[0] === "feats" && parts.length === 1) return { page: "feats" };
  return { page: "lost" };
}

// Draws the page the address names. The panels rise into place only when arriving,
// not when the numbers are refreshed under a page that is already open.
let current = { page: "home" };
function draw(arriving) {
  view.classList.toggle("still", !arriving);
  gradients = 0;
  if (state !== "ready") {
    view.replaceChildren(state === "waiting" ? quiet("Lush Acres")
      : quiet("Lush Acres", "Nothing counted yet. The numbers appear a few minutes after the first member has played."));
    return;
  }
  current = route(location.pathname);
  const pages = { home: homePage, members: membersPage, records: recordsPage, feats: featsPage, lost: lostPage };
  view.replaceChildren(current.member ? memberPage(current.member) : pages[current.page]());
  for (const a of document.querySelectorAll("#nav a")) {
    if (a.dataset.page === current.page) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  }
  const names = { home: "", members: "Members", records: "Records", feats: "Feats", lost: "Nothing here" };
  const name = current.member ? current.member.name : names[current.page];
  document.title = name ? name + " · Lush Acres" : "Lush Acres";
}

// Where a page is among the others, to tell which way to move between two.
const place = (where) => ({ home: 0, members: 1, records: 2, feats: 3, lost: 4 })[where.page] + (where.member ? 0.5 : 0);

// Moves to the page the address now names, with a transition where the browser
// has them: the sky stays, the page slides the way it lies from the last one,
// and a member's figure walks from where it stood to where it stands on the next
// page. The stylesheet names the big figure on a member's page, and here the
// small one they came from or go back to is named.
const marked = () => view.querySelectorAll('canvas[style*="view-transition-name"]');
function move(back, scroll) {
  const from = current;
  const change = () => {
    draw(true);
    window.scrollTo(0, scroll || 0);
    if (from.member && !current.member) {
      const walker = view.querySelector(`a[data-uuid="${from.member.uuid}"] canvas.body`);
      if (walker) walker.style.viewTransitionName = "figure";
    }
    const way = place(current) - place(from);
    document.documentElement.dataset.way = (way ? way < 0 : back) ? "back" : "on";
  };
  const settle = () => marked().forEach((canvas) => canvas.style.removeProperty("view-transition-name"));
  if (!document.startViewTransition || calm.matches) {
    change();
    settle();
    return;
  }
  document.startViewTransition(change).finished.finally(settle);
}

// How many pages deep into the visit this is, to tell going back from going on,
// and how far down each page was when it was left, to come back to the same spot.
let depth = 0;
history.scrollRestoration = "manual";
history.replaceState({ depth }, "");
document.addEventListener("click", (event) => {
  const a = event.target.closest("a[href]");
  if (!a || event.defaultPrevented || event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || a.target) return;
  const url = new URL(a.href);
  // The map, the numbers and other sites are not pages of this file.
  if (url.origin !== location.origin || url.pathname.startsWith("/map/") || url.pathname.startsWith("/data/")) return;
  event.preventDefault();
  if (url.pathname === location.pathname) {
    window.scrollTo({ top: 0, behavior: "smooth" });
    return;
  }
  marked().forEach((canvas) => canvas.style.removeProperty("view-transition-name"));
  const walker = a.dataset.uuid && a.querySelector("canvas.body");
  if (walker) walker.style.viewTransitionName = "figure";
  history.replaceState({ depth, scroll: window.scrollY }, "");
  depth += 1;
  history.pushState({ depth }, "", url.pathname);
  move(false);
});
window.addEventListener("popstate", (event) => {
  const to = (event.state && event.state.depth) || 0;
  const back = to < depth;
  depth = to;
  move(back, event.state && event.state.scroll);
});
// A turned phone or a resized window may fit more or fewer in a row of the meadow.
window.addEventListener("resize", () => {
  if (state === "ready" && current.page === "home" && Math.abs(Math.min(window.innerWidth, 1180) - 24 - fitted) > 40) draw(false);
});

// The feats come in a file of their own, and the hub does without them: a file
// that is missing or cannot be read leaves what was loaded before.
async function loadFeats() {
  try {
    const response = await fetch("/data/feats.json", { cache: "no-store" });
    if (!response.ok) throw new Error(response.status);
    const data = await response.json();
    const members = new Map(data.members.map((member) => [member.uuid, member.earned]));
    feats = data.feats;
    held = members;
  } catch (error) {
    // Nothing to do.
  }
}

async function load() {
  try {
    const response = await fetch("/data/stats.json", { cache: "no-store" });
    if (!response.ok) throw new Error(response.status);
    const data = await response.json();
    await loadFeats();
    // Someone who joined and left at once has a file with no play time in it.
    const found = data.players
      .map((p) => ({ ...p, name: p.name || p.uuid.slice(0, 8), country: /^[A-Z]{2}$/.test(p.country || "") ? p.country : null }))
      .filter((p) => custom(p, "play_time") > 0);
    if (!found.length) throw new Error("empty");
    const first = state !== "ready";
    players = found;
    updated = data.updated;
    state = "ready";
    document.getElementById("updated").textContent =
      "Last counted " + new Date(updated * 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) + ".";
    draw(first);
  } catch (error) {
    // Keep showing what was loaded before, if anything.
    if (state === "ready") return;
    state = "empty";
    draw(false);
  }
}

draw(false);
load();
setInterval(load, 5 * 60 * 1000);
