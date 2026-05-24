(function(){
  "use strict";

  const BONE_ALIASES = {
    hips: ["hips", "pelvis", "root"],
    spine: ["spine", "spine_01", "spine_02", "spine_03"],
    neck: ["neck", "neck_01"],
    head: ["head"],
    leftShoulder: ["clavicle_l", "shoulder_l", "leftShoulder"],
    leftUpperArm: ["upperarm_l", "leftUpperArm"],
    leftLowerArm: ["lowerarm_l", "leftLowerArm"],
    leftHand: ["hand_l", "leftHand"],
    rightShoulder: ["clavicle_r", "shoulder_r", "rightShoulder"],
    rightUpperArm: ["upperarm_r", "rightUpperArm"],
    rightLowerArm: ["lowerarm_r", "rightLowerArm"],
    rightHand: ["hand_r", "rightHand"],
    leftUpperLeg: ["thigh_l", "leftUpperLeg"],
    leftLowerLeg: ["calf_l", "leftLowerLeg"],
    leftFoot: ["foot_l", "leftFoot"],
    rightUpperLeg: ["thigh_r", "rightUpperLeg"],
    rightLowerLeg: ["calf_r", "rightLowerLeg"],
    rightFoot: ["foot_r", "rightFoot"],
  };

  function normalizeName(value){
    return String(value || "").toLowerCase().replace(/[^a-z0-9]+/g, "");
  }

  function normalizeLandmarks(landmarks){
    const result = {};
    if (!landmarks || typeof landmarks !== "object") return result;
    Object.keys(landmarks).forEach(name => {
      const item = landmarks[name];
      if (Array.isArray(item)){
        result[name] = {x: Number(item[0]) || 0, y: Number(item[1]) || 0, z: Number(item[2]) || 0, confidence: Number(item[3] == null ? 1 : item[3])};
      } else if (item && typeof item === "object"){
        result[name] = {x: Number(item.x) || 0, y: Number(item.y) || 0, z: Number(item.z) || 0, confidence: Number(item.confidence == null ? 1 : item.confidence)};
      }
    });
    return result;
  }

  function mapBones(bones){
    const list = Array.isArray(bones) ? bones : [];
    const byName = new Map();
    list.forEach(bone => byName.set(normalizeName(bone && bone.name), bone));
    const mapped = {};
    const missing = [];
    Object.keys(BONE_ALIASES).forEach(role => {
      const found = BONE_ALIASES[role].map(normalizeName).map(name => byName.get(name)).find(Boolean);
      if (found) mapped[role] = found;
      else missing.push(role);
    });
    return {mapped, missing, complete: missing.length === 0};
  }

  function vectorBetween(a, b){
    if (!a || !b) return null;
    return {x: b.x - a.x, y: b.y - a.y, z: (b.z || 0) - (a.z || 0)};
  }

  function calculateTargetVectors(landmarks){
    const lm = normalizeLandmarks(landmarks);
    return {
      leftUpperArm: vectorBetween(lm.left_shoulder || lm.leftShoulder, lm.left_elbow || lm.leftElbow),
      leftLowerArm: vectorBetween(lm.left_elbow || lm.leftElbow, lm.left_wrist || lm.leftWrist),
      rightUpperArm: vectorBetween(lm.right_shoulder || lm.rightShoulder, lm.right_elbow || lm.rightElbow),
      rightLowerArm: vectorBetween(lm.right_elbow || lm.rightElbow, lm.right_wrist || lm.rightWrist),
      leftUpperLeg: vectorBetween(lm.left_hip || lm.leftHip, lm.left_knee || lm.leftKnee),
      leftLowerLeg: vectorBetween(lm.left_knee || lm.leftKnee, lm.left_ankle || lm.leftAnkle),
      rightUpperLeg: vectorBetween(lm.right_hip || lm.rightHip, lm.right_knee || lm.rightKnee),
      rightLowerLeg: vectorBetween(lm.right_knee || lm.rightKnee, lm.right_ankle || lm.rightAnkle),
    };
  }

  window.AvatarRetarget = {
    BONE_ALIASES,
    normalizeLandmarks,
    mapBones,
    calculateTargetVectors,
  };
})();
