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
const total = (list) => list.reduce((a, b) => a + b, 0);
const count = (n) => number.format(n);
// A height in the world, with a proper minus sign below zero.
const height = (y) => "y " + (y < 0 ? "\u2212" + count(-y) : count(y));
const calm = matchMedia("(prefers-reduced-motion: reduce)");
// Times are shown in the visitor's own time, on a twelve-hour clock for those who use one.
const clock = new Intl.DateTimeFormat([], { hour: "numeric" }).resolvedOptions().hour12 ? "en-US" : "en-GB";

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

// A block or an item as the game names it: "minecraft:spruce_planks" becomes "Spruce planks".
const thing = (name) => pretty(name.replace(/^minecraft:/, ""));

// What gets us, and what a member has hunted. A creature as the game names it is
// put in the plural, so "cave_spider" becomes "Cave spiders", and the world's own
// causes, such as "fall", get a word of their own.
const CAUSES = {
  fall: "Falling", lava: "Lava", drowning: "Drowning", fire: "Fire", "fire tick": "Burning",
  suffocation: "Suffocating", starvation: "Hunger", void: "The void", "block explosion": "Explosions",
  "entity explosion": "Explosions", lightning: "Lightning", magic: "Magic", poison: "Poison",
  wither: "Withering", freeze: "Freezing", contact: "Prickly plants", "fly into wall": "Flying into walls",
  "hot floor": "Magma blocks", "falling block": "Falling blocks", cramming: "Crowding",
  "dragon breath": "Dragon's breath", thorns: "Thorns", "sonic boom": "Sonic booms",
  campfire: "Campfires", "world border": "The world border",
};
const CREATURES = {
  wolf: "Wolves", ender_dragon: "The Ender Dragon", drowned: "Drowned", bogged: "Bogged",
  silverfish: "Silverfish", pufferfish: "Pufferfish", tropical_fish: "Tropical fish", cod: "Cod",
  salmon: "Salmon", sheep: "Sheep", squid: "Squid", glow_squid: "Glow squid",
};
function creatures(name) {
  const id = name.replace(/^minecraft:/, "");
  if (CAUSES[id]) return CAUSES[id];
  if (CREATURES[id]) return CREATURES[id];
  if (/[^a-z_]/.test(id)) return pretty(id);
  if (id.endsWith("man")) return pretty(id.slice(0, -3) + "men");
  return pretty(id + (/(s|x|ch|sh)$/.test(id) ? "es" : "s"));
}

function country(code) {
  try { return regions.of(code) || code; } catch (error) { return code; }
}

// A date keeps its day and month on one line.
const unbroken = (text) => text.replace(/(\d+) /, "$1\u00a0");

// A day of the server's calendar, "2026-11-13", as "13 Nov", or in full as
// "Friday 13 November". The year shows only when it is not this one.
function dayName(day, full) {
  const date = new Date(day + "T12:00:00Z");
  const options = { day: "numeric", month: full ? "long" : "short", timeZone: "UTC" };
  if (full) options.weekday = "long";
  if (date.getUTCFullYear() !== new Date().getFullYear()) options.year = "numeric";
  return unbroken(date.toLocaleDateString("en-GB", options));
}

// An hour, given by when it began in seconds, in the visitor's own time: the day,
// and from when to when.
function hourName(at, full) {
  const from = new Date(at * 1000);
  const options = { day: "numeric", month: full ? "long" : "short" };
  if (full) options.weekday = "long";
  if (from.getFullYear() !== new Date().getFullYear()) options.year = "numeric";
  const day = unbroken(from.toLocaleDateString("en-GB", options));
  if (!full) return day;
  const time = (date) => date.toLocaleTimeString(clock, { hour: "numeric", minute: "2-digit" });
  return day + ", " + time(from) + " to " + time(new Date(at * 1000 + 3600000));
}

// An hour of the day, 0 to 23, as the visitor's clock shows it.
const hourOfDay = (h) => new Date(2026, 0, 1, h).toLocaleTimeString(clock, clock === "en-US" ? { hour: "numeric" } : { hour: "2-digit", minute: "2-digit" });

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

let players = [];
// "waiting" until the numbers have come once, then "ready", or "empty" if there are none.
let state = "waiting";
// When the numbers were written. A file saved within a few minutes of that belongs
// to someone who is playing, because the game saves everyone who is online together.
let updated = 0;
const playing = (p) => p.seen && updated - p.seen < 7 * 60;
const home = (p) => "/members/" + encodeURIComponent(p.name);
const status = (p) => (playing(p) ? "Playing now" : "Last played " + ago(p.seen));
const dot = () => make("i", "live");
// Members in the order of their names, which is the order lists of them keep. A
// Bedrock player's name starts with a dot, which is not counted.
const byName = (a, b) => a.name.replace(/^\./, "").localeCompare(b.name.replace(/^\./, ""), "en", { sensitivity: "base" });

// What the ledger has counted from the server's log, which the script fetches (see
// README): blocks placed and broken, the server's days and moments, and for each
// member by their id what they built with, hunted and traded for, their hours and
// their own bests. Null until it has come, and the hub shows what it can without it.
// Time played and when someone last played come from the game's own files instead.
let counted = null;
let tallies = new Map();
const tally = (p) => tallies.get(p.uuid) || null;

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

// Small drawings for the moments, in the style of the row of pages.
const DRAWINGS = {
  day: '<rect x="8" y="8" width="8" height="8" rx="1"/><path d="M12 2.5V5M12 19v2.5M2.5 12H5M19 12h2.5M5.3 5.3l1.8 1.8M16.9 16.9l1.8 1.8M5.3 18.7l1.8-1.8M16.9 7.1l1.8-1.8"/>',
  hour: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
  together: '<circle cx="9" cy="8.2" r="3.2"/><path d="M3 19.5c0-3.2 2.7-5.3 6-5.3s6 2.1 6 5.3M15.5 5.3a3.2 3.2 0 0 1 0 5.8M17.5 14.7c2.1.7 3.5 2.4 3.5 4.8"/>',
  deep: '<path d="M3.5 6.5h17M12 9.5v10M8.5 16 12 19.5l3.5-3.5"/>',
  high: '<path d="M3.5 19.5h17M12 16.5v-11M8.5 9 12 5.5 15.5 9"/>',
};
function drawn(name) {
  const node = document.createElementNS(NS, "svg");
  node.setAttribute("viewBox", "0 0 24 24");
  node.setAttribute("class", "mark");
  node.setAttribute("aria-hidden", "true");
  node.innerHTML = DRAWINGS[name];
  return node;
}

// The moments worth remembering, for the whole server and with no names: the
// biggest day, the busiest hour, the hour with most of us at work, and the deepest
// dig and highest build anyone has made. Each has a short way and a full way to
// say when. Hours are shown in the visitor's own time.
function moments() {
  if (!counted) return [];
  const { biggestDay, busiestHour, fullestHour } = counted.moments || {};
  const all = [];
  if (biggestDay) all.push({ drawing: "day", label: "The biggest day", value: count(biggestDay.blocks) + " blocks",
    when: dayName(biggestDay.day), told: "placed and broken on " + dayName(biggestDay.day, true) });
  if (busiestHour) all.push({ drawing: "hour", label: "The busiest hour", value: count(busiestHour.blocks) + " blocks",
    when: hourName(busiestHour.at), told: "placed and broken in one hour, " + hourName(busiestHour.at, true), hour: true });
  if (fullestHour) all.push({ drawing: "together", label: "The fullest hour", value: fullestHour.members === 1 ? "One of us" : count(fullestHour.members) + " of us",
    when: hourName(fullestHour.at), told: "at work in the same hour, " + hourName(fullestHour.at, true), hour: true });
  const bests = counted.members.map((t) => t.bests || {});
  const deep = bests.map((b) => b.deepest).filter((y) => typeof y === "number");
  const high = bests.map((b) => b.highest).filter((y) => typeof y === "number");
  if (deep.length) all.push({ drawing: "deep", label: "The deepest dig", value: height(Math.min(...deep)), told: "the deepest anyone has dug" });
  if (high.length) all.push({ drawing: "high", label: "The highest build", value: height(Math.max(...high)), told: "the highest anyone has built" });
  return all;
}

// When a member plays, from the things they did in each hour of the day: the hours
// moved into the visitor's own time, and the part of the day that holds most of
// them, if one holds at least a good third.
const PARTS = [["at night", 0], ["in the morning", 6], ["in the afternoon", 12], ["in the evening", 18]];
function rhythm(t) {
  if (!t || !Array.isArray(t.hours) || t.hours.length !== 24) return null;
  const shift = -new Date().getTimezoneOffset() / 60;
  const hours = new Array(24).fill(0);
  t.hours.forEach((n, h) => { hours[((Math.floor(h + shift) % 24) + 24) % 24] += n; });
  const all = total(hours);
  if (!all) return null;
  const parts = PARTS.map(([words, from]) => ({ words, from, share: total(hours.slice(from, from + 6)) / all }));
  const most = parts.reduce((a, b) => (b.share > a.share ? b : a));
  const clear = most.share >= 0.35;
  return { hours, from: clear ? most.from : null, words: clear ? "Mostly here " + most.words : "Here at all hours" };
}

// One line about a member from what the ledger counts, with no number in it: when
// they are usually about, which also tells the others when to find them.
function gist(p) {
  const t = tally(p);
  const when = rhythm(t);
  if (when) return when.words;
  if (t && t.buildsWith && t.buildsWith.length) return "Builds mostly with " + thing(t.buildsWith[0].name).toLowerCase();
  return null;
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

// A short list of things and how many of each, largest first, with a bar for each
// measured against the first. The numbers are one member's or the whole server's,
// and never set one member against another.
function bars(entries, show) {
  const top = entries.filter(([, n]) => n > 0).sort((a, b) => b[1] - a[1]).slice(0, 12);
  if (!top.length) return null;
  const ol = make("ol", "bars");
  for (const [name, n] of top) {
    const li = make("li", null, make("span", null, name), make("b", null, (show || count)(n)));
    li.style.setProperty("--share", (100 * n / top[0][1]).toFixed(1) + "%");
    ol.append(li);
  }
  return ol;
}

// Bars standing side by side, as tall as their numbers, for days or hours. Those
// named in "lit" stand out, and "gold" marks one.
function strip(className, values, lit, gold, label) {
  const most = Math.max(1, ...values);
  const box = make("div", "strip " + className);
  values.forEach((n, index) => {
    const bar = make("i", index === gold ? "gold" : lit(index) ? "lit" : null);
    bar.style.height = (100 * n / most).toFixed(1) + "%";
    box.append(bar);
  });
  box.setAttribute("role", "img");
  box.setAttribute("aria-label", label);
  return box;
}

// The way to a million blocks placed and broken, all of us together, notched every
// hundred thousand like the bar of experience in the game.
const MILLION = 1e6;
function road() {
  const done = counted.placed + counted.broken;
  const reached = done >= MILLION;
  const track = make("div", reached ? "track reached" : "track", make("i"));
  track.firstChild.style.width = Math.min(100, 100 * done / MILLION).toFixed(2) + "%";
  track.setAttribute("role", "img");
  track.setAttribute("aria-label", reached ? "Reached" : Math.floor(100 * done / MILLION) + "% of the way");
  return make("section", "card road",
    make("h2", null, "The road to the first million"),
    make("p", "sofar", make("strong", null, count(done)), make("span", null, "blocks placed and broken" + (reached ? ", all of us together" : " so far"))),
    track,
    reached
      ? make("p", "ends", make("span", "done", "The first million is reached"))
      : make("p", "ends", make("span", null, Math.floor(100 * done / MILLION) + "% of the way"), make("span", null, count(MILLION - done) + " to go")));
}

// The server's days, the latest four weeks of them, with the biggest day marked.
// Days are counted by the calendar, so a day with nothing in it shows as a gap.
function daysCard() {
  const days = (counted.days || []).filter((d) => /^\d{4}-\d{2}-\d{2}$/.test(d.day));
  if (!days.length) return null;
  const known = new Map(days.map((d) => [d.day, d.placed + d.broken]));
  const at = (day) => Date.parse(day + "T00:00:00Z");
  const last = at(days[days.length - 1].day);
  const first = Math.max(at(days[0].day), last - 27 * 86400000);
  const shown = [];
  for (let t = first; t <= last; t += 86400000) {
    const day = new Date(t).toISOString().slice(0, 10);
    shown.push({ day, blocks: known.get(day) || 0 });
  }
  const big = counted.moments && counted.moments.biggestDay;
  const gold = big ? shown.findIndex((d) => d.day === big.day) : -1;
  const since = first === at(days[0].day);
  const label = "Blocks placed and broken each day from " + dayName(shown[0].day, true) + " to " + dayName(shown[shown.length - 1].day, true);
  return make("section", "card days",
    make("h2", null, "Day by day"),
    make("p", "note", since ? "Blocks placed and broken each day since " + dayName(days[0].day) + "." : "Blocks placed and broken each day, the latest four weeks."),
    strip("daily", shown.map((d) => d.blocks), () => true, gold, label),
    make("p", "ends", make("span", null, dayName(shown[0].day)), make("span", null, dayName(shown[shown.length - 1].day))),
    big && make("p", "key", gold >= 0 && make("i"), "The biggest day, " + dayName(big.day, true) + ": " + count(big.blocks) + " blocks."));
}

// A row of the noticeboard: a moment, the way a member's row has a face.
function notice(moment) {
  return make("li", null, link("/moments", null, drawn(moment.drawing),
    make("span", null, make("b", null, moment.value), make("small", null, moment.label)), moment.when && make("em", null, moment.when)));
}

function homePage() {
  const hours = Math.round(players.reduce((a, p) => a + custom(p, "play_time"), 0) / 72000);
  const here = players.filter(playing).length;
  const words = make("div", "words",
    make("p", "over", "Welcome back to"),
    make("h1", null, "Lush Acres"),
    // A number keeps to the word after it, so a line never breaks between them.
    make("p", "tally", counted
      ? count(hours) + "\u00a0hours together, " + count(counted.placed) + "\u00a0blocks placed and " + count(counted.broken) + "\u00a0broken."
      : count(hours) + "\u00a0hours together on the acres."),
    make("p", here ? "now on" : "now", here ? dot() : null,
      here ? (here === 1 ? "One of us is playing right now" : here + " of us are playing right now") : "The meadow is quiet right now"),
  );

  const recent = make("ul", "people");
  [...players].sort((a, b) => (b.seen || 0) - (a.seen || 0)).slice(0, 5)
    .forEach((p) => recent.append(person(p, playing(p) ? "Playing now" : ago(p.seen))));
  const around = make("section", "card", make("h2", null, "Around the acres"), recent, link("/members", "more", "Everyone", arrow()));

  const remembered = moments();
  const board = remembered.length
    ? make("section", "card", make("h2", null, "On the noticeboard"), make("ul", "people", ...remembered.slice(0, 4).map(notice)),
      link("/moments", "more", "All the moments", arrow()))
    : null;

  const earned = make("ul", "people");
  const newest = earnings().slice(0, 5);
  newest.forEach((one) => earned.append(person(one.p, ago(one.at), one.info.name)));
  const lately = newest.length
    ? make("section", "card", make("h2", null, "Lately earned"), earned, link("/feats", "more", "All the feats", arrow()))
    : null;

  // What all of us have done together, from what the ledger counts.
  let together = null;
  if (counted) {
    // The eight most used, as in a member's own lists, so the card stays short on a phone.
    const built = bars(counted.builtFrom.most.slice(0, 8).map((e) => [thing(e.name), e.count]));
    // What the ledger could not put a name to, and a member killed by another member,
    // are left out: the first says nothing and the second would point at someone.
    const known = counted.whatGetsUs.filter((e) => e.name !== "unknown" && e.name !== "another member");
    const gets = bars(known.map((e) => [creatures(e.name), e.count]), (n) => count(n) + (n === 1 ? " time" : " times"));
    together = make("div", "together", road(), daysCard(),
      built && make("section", "card", make("h2", null, "What Lush Acres is built from"),
        make("p", "note", count(counted.builtFrom.kinds) + " kinds of block placed in all."), built),
      gets && make("section", "card", make("h2", null, "What gets us"),
        make("p", "note", "The usual suspects, for all of us together."), gets));
  }

  const doors = make("div", "doors");
  for (const [href, name, text] of [
    ["/members", "Members", "Everyone who calls the acres home."],
    remembered.length && ["/moments", "Moments", "Days and hours to remember."],
    feats.length && ["/feats", "Feats", "Earned alone and together."],
    ["/map/", "The map", "See what everyone has built."],
  ].filter(Boolean)) {
    const picture = document.querySelector(`#nav a[href="${href}"] svg`).cloneNode(true);
    doors.append(link(href, "card door", picture, make("span", null, make("strong", null, name), make("small", null, text)), arrow()));
  }

  const box = new DocumentFragment();
  box.append(scene("home", 3, words, ...lineup(40)), make("div", "page",
    staggered(make("div", "two", around, board, lately)), together && staggered(together), staggered(doors)));
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
  [...players].sort(byName).forEach((p, index) => {
    const where = make("span", playing(p) ? "status on" : "status", playing(p) && dot(), playing(p) ? "Playing now" : "Last played " + ago(p.seen));
    const line = gist(p);
    const a = link(home(p), "card member",
      plot(100 + index * 13, figure(p, "body", 132)),
      called("strong", p, p.country && flag(p.country)),
      where,
      line && make("small", null, line),
      p.name.startsWith(".") && make("span", "chip", "Bedrock"));
    a.dataset.uuid = p.uuid;
    grid.append(a);
  });
  const words = make("div", "words", make("h1", null, "Members"), make("p", "tally", "Everyone who calls the acres home."));
  const box = new DocumentFragment();
  const told = players.some((p) => rhythm(tally(p))) && make("p", "aside", "Times of day are shown in your own time.");
  box.append(scene("sub", 11, words, acre(3, 60, true)), make("div", "page", staggered(grid), told));
  return box;
}

// A panel on a member's page with a list of what they did most. Nothing when the
// list is empty.
function list(title, entries, note) {
  const ol = bars(entries);
  return ol && make("section", "card", make("h3", null, title), note && make("p", "note", note), ol);
}

// When a member plays, as a line and as the hours of the day in the visitor's time.
function whenCard(when) {
  const lit = (h) => when.from === null || (h >= when.from && h < when.from + 6);
  return make("section", "card when",
    make("h3", null, when.words),
    strip("hourly", when.hours, lit, -1, "When they play, hour by hour"),
    make("p", "ticks", ...[0, 6, 12, 18].map((h) => make("span", null, hourOfDay(h)))),
    make("p", "note", "Shown in your own time."));
}

// A member's own bests, each only when there is one.
function bestsCard(t) {
  const bests = t.bests || {};
  const ol = make("ol", "bare");
  if (bests.buildingDay) {
    ol.append(make("li", null, make("span", null, "Biggest building day", make("small", null, dayName(bests.buildingDay.day, true))),
      make("b", null, count(bests.buildingDay.placed) + " blocks")));
  }
  if (typeof bests.deepest === "number") ol.append(make("li", null, make("span", null, "Deepest dig"), make("b", null, height(bests.deepest))));
  if (typeof bests.highest === "number") ol.append(make("li", null, make("span", null, "Highest build"), make("b", null, height(bests.highest))));
  return ol.children.length ? make("section", "card", make("h3", null, "Own bests"), ol) : null;
}

// The members someone has built beside, each a way to their page. Nothing when there
// are none.
function besideCard(t) {
  const others = (t.beside || []).map(byId).filter(Boolean).sort(byName);
  if (!others.length) return null;
  const faces = make("div", "holders");
  others.forEach((other) => faces.append(link(home(other), null, figure(other, "head", 28), make("b", null, other.name))));
  return make("section", "card", make("h3", null, "Has built beside"), faces);
}

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
  const facts = make("p", "facts",
    make("span", playing(p) ? "status on" : "status", playing(p) && dot(), status(p)),
    p.country && make("span", null, flag(p.country), "Joins from " + country(p.country)),
    p.name.startsWith(".") && make("span", null, "Plays on Bedrock"));
  const words = make("div", "words", link("/members", "back", arrow(), "Members"), called("h1", p), facts);
  const stage = make("div", "stage", star(p), make("i", "shade"));

  // Their own numbers, with nothing to say how they compare: time played from the
  // game, and the rest from the ledger, when it has counted something of theirs.
  const t = tally(p);
  const tile = (label, value) => make("div", "card tile", make("small", null, label), make("strong", null, value));
  const tiles = make("div", t ? "tiles" : "tiles alone", tile("Time played", duration(custom(p, "play_time"))));
  if (t) {
    tiles.append(tile("Blocks placed", count(t.placed)), tile("Blocks broken", count(t.broken)),
      tile("Mobs killed", count(t.mobsKilled)), tile("Villager trades", count(t.trades)));
  }

  const when = rhythm(t);
  const lists = make("div", "lists",
    when && whenCard(when),
    t && list("Builds with", t.buildsWith.map((e) => [thing(e.name), e.count]),
      t.kinds ? count(t.kinds) + (t.kinds === 1 ? " kind" : " kinds") + " of block in all" : null),
    t && list("Mobs killed", t.mobs.map((e) => [creatures(e.name), e.count])),
    t && list("Gets from villagers", t.gets.map((e) => [thing(e.name), e.count])),
    t && bestsCard(t),
    t && besideCard(t),
  );

  // On to the members either side, in the order the members page has them.
  const everyone = [...players].sort(byName);
  const index = everyone.indexOf(p);
  const beside = make("nav", "beside");
  beside.setAttribute("aria-label", "Other members");
  for (const [other, way] of [[everyone[index - 1], "before"], [everyone[index + 1], "after"]]) {
    if (other) beside.append(link(home(other), "card " + way, arrow(), figure(other, "head", 40), make("b", null, other.name)));
  }

  const box = new DocumentFragment();
  box.append(scene("sub solo", 20 + index, words, acre(3, 80 + index, true, stage)),
    make("div", "page", staggered(tiles), featsOf(p), lists.children.length ? staggered(lists) : null, beside));
  return box;
}

// Moments to remember on the acres, for all of us together and with no names.
function momentsPage() {
  const all = moments();
  if (!all.length) return quiet("Moments", "No moments to show right now.", link("/", "button", arrow(), "Back to the meadow"));
  const grid = make("div", "moments");
  for (const moment of all) {
    grid.append(make("section", "card moment", drawn(moment.drawing),
      make("span", null, make("small", null, moment.label), make("strong", null, moment.value), make("em", null, moment.told))));
  }
  const words = make("div", "words", make("h1", null, "Moments"), make("p", "tally", "Days and hours to remember on the acres."));
  const box = new DocumentFragment();
  box.append(scene("sub", 31, words, acre(3, 70, true)), make("div", "page", staggered(grid),
    all.some((moment) => moment.hour) && make("p", "aside", "Hours are shown in your own time.")));
  return box;
}

// A page with nothing on it but a few words: an address that names no page, and
// the page before the numbers have come.
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
  // The moments were the records once, and an old link to those still finds them.
  if ((parts[0] === "moments" || parts[0] === "records") && parts.length === 1) return { page: "moments" };
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
  // An old address shows the page's own in the address bar, to be shared from there.
  if (current.page === "moments" && location.pathname !== "/moments") history.replaceState(history.state, "", "/moments");
  const pages = { home: homePage, members: membersPage, moments: momentsPage, feats: featsPage, lost: lostPage };
  view.replaceChildren(current.member ? memberPage(current.member) : pages[current.page]());
  for (const a of document.querySelectorAll("#nav a")) {
    if (a.dataset.page === current.page) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  }
  const names = { home: "", members: "Members", moments: "Moments", feats: "Feats", lost: "Nothing here" };
  const name = current.member ? current.member.name : names[current.page];
  document.title = name ? name + " · Lush Acres" : "Lush Acres";
}

// Where a page is among the others, to tell which way to move between two.
const place = (where) => ({ home: 0, members: 1, moments: 2, feats: 3, lost: 4 })[where.page] + (where.member ? 0.5 : 0);

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

// What the ledger counts comes the same way, and the hub does without it too.
async function loadCounted() {
  try {
    const response = await fetch("/data/counted.json", { cache: "no-store" });
    if (!response.ok) throw new Error(response.status);
    const data = await response.json();
    // The script checks every part, and this is all the page relies on being there.
    const lists = [data.members, data.days, data.whatGetsUs, data.builtFrom && data.builtFrom.most];
    if (typeof data.placed !== "number" || typeof data.broken !== "number" || !lists.every(Array.isArray)) throw new Error("not counted");
    if (!data.members.every((t) => ["mobs", "gets", "buildsWith", "beside"].every((key) => Array.isArray(t[key])))) throw new Error("not counted");
    tallies = new Map(data.members.map((member) => [member.uuid, member]));
    counted = data;
  } catch (error) {
    // Nothing to do.
  }
}

async function load() {
  try {
    const response = await fetch("/data/stats.json", { cache: "no-store" });
    if (!response.ok) throw new Error(response.status);
    const data = await response.json();
    await Promise.all([loadFeats(), loadCounted()]);
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
