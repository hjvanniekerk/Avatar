(function(){
  "use strict";

  function v3(x, y, z){ return {x: Number(x) || 0, y: Number(y) || 0, z: Number(z) || 0}; }
  function sub(a, b){ return v3(a.x - b.x, a.y - b.y, a.z - b.z); }
  function add(a, b){ return v3(a.x + b.x, a.y + b.y, a.z + b.z); }
  function scale(a, s){ return v3(a.x * s, a.y * s, a.z * s); }
  function length(a){ return Math.hypot(a.x, a.y, a.z); }
  function normalize(a){
    const n = length(a) || 1;
    return scale(a, 1 / n);
  }
  function dot(a, b){ return a.x * b.x + a.y * b.y + a.z * b.z; }
  function cross(a, b){
    return v3(a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x);
  }

  function quaternionFromVectors(from, to){
    const a = normalize(from);
    const b = normalize(to);
    const d = Math.max(-1, Math.min(1, dot(a, b)));
    if (d > 0.9999) return {x: 0, y: 0, z: 0, w: 1};
    if (d < -0.9999){
      const axis = Math.abs(a.x) < 0.8 ? normalize(cross(a, v3(1, 0, 0))) : normalize(cross(a, v3(0, 1, 0)));
      return {x: axis.x, y: axis.y, z: axis.z, w: 0};
    }
    const axis = cross(a, b);
    const s = Math.sqrt((1 + d) * 2);
    return {x: axis.x / s, y: axis.y / s, z: axis.z / s, w: s * 0.5};
  }

  function solveTwoBone(start, mid, end, target, pole, lengths){
    const upper = Number(lengths && lengths.upper) || length(sub(mid, start));
    const lower = Number(lengths && lengths.lower) || length(sub(end, mid));
    const toTarget = sub(target, start);
    const distance = Math.max(0.0001, Math.min(length(toTarget), upper + lower - 0.0001));
    const dir = normalize(toTarget);
    const poleDir = pole ? normalize(sub(pole, start)) : v3(0, 1, 0);
    const side = normalize(cross(dir, poleDir));
    const bend = normalize(cross(side, dir));
    const cosAngle = Math.max(-1, Math.min(1, (upper * upper + distance * distance - lower * lower) / (2 * upper * distance)));
    const along = upper * cosAngle;
    const height = Math.sqrt(Math.max(0, upper * upper - along * along));
    const solvedMid = add(add(start, scale(dir, along)), scale(bend, height));
    return {
      mid: solvedMid,
      end: target,
      upperQuaternion: quaternionFromVectors(sub(mid, start), sub(solvedMid, start)),
      lowerQuaternion: quaternionFromVectors(sub(end, mid), sub(target, solvedMid)),
    };
  }

  window.AvatarIK = {
    v3,
    sub,
    add,
    scale,
    length,
    normalize,
    quaternionFromVectors,
    solveTwoBone,
  };
})();
