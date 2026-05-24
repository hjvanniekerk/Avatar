(function(){
  "use strict";

  function collectBones(root, out, seen){
    if (!root || typeof root !== "object" || seen.has(root)) return;
    seen.add(root);
    if (root.isBone || root.type === "Bone") out.push(root);
    if (root.skeleton && Array.isArray(root.skeleton.bones)) root.skeleton.bones.forEach(bone => out.push(bone));
    if (Array.isArray(root.children)) root.children.forEach(child => collectBones(child, out, seen));
  }

  function sceneCandidatesFromModelViewer(model){
    return [
      model && model.model && model.model.scene,
      model && model.scene,
      model && model.threeScene,
    ].filter(Boolean);
  }

  function diagnosticsFromModelViewer(model){
    const bones = [];
    sceneCandidatesFromModelViewer(model).forEach(scene => collectBones(scene, bones, new Set()));
    const uniqueBones = Array.from(new Set(bones));
    const mapped = window.AvatarRetarget ? window.AvatarRetarget.mapBones(uniqueBones) : {mapped: {}, missing: [], complete: false};
    return {
      sceneAccessible: uniqueBones.length > 0,
      boneCount: uniqueBones.length,
      boneNames: uniqueBones.map(bone => bone && bone.name).filter(Boolean),
      mappedBones: Object.keys(mapped.mapped || {}),
      missingBones: mapped.missing || [],
      boneMapComplete: !!mapped.complete,
      boneRetargetActive: false,
      ikSolverActive: false,
      reason: uniqueBones.length
        ? "Bones are visible to browser diagnostics; solver is scaffolded but non-mutating until a verified retarget path is enabled."
        : "No browser-accessible bones found from model-viewer; solver cannot mutate GLB limbs.",
    };
  }

  function calculateQuaternionDeltas(currentVectors, targetVectors){
    const result = {};
    if (!window.AvatarIK) return result;
    Object.keys(targetVectors || {}).forEach(key => {
      if (!currentVectors || !currentVectors[key] || !targetVectors[key]) return;
      result[key] = window.AvatarIK.quaternionFromVectors(currentVectors[key], targetVectors[key]);
    });
    return result;
  }

  function solveFrame(options){
    const model = options && options.model;
    const landmarks = options && options.landmarks;
    const diagnostics = diagnosticsFromModelViewer(model);
    const targetVectors = window.AvatarRetarget ? window.AvatarRetarget.calculateTargetVectors(landmarks || {}) : {};
    return {
      applied: false,
      diagnostics,
      targetVectors,
      reason: diagnostics.sceneAccessible
        ? "Solver scaffold produced target vectors only; destructive bone mutation is disabled until GLB bone map is complete."
        : "Cannot solve real limb IK because browser has no access to GLB bones.",
    };
  }

  window.AvatarSkeletalSolver = {
    diagnosticsFromModelViewer,
    calculateQuaternionDeltas,
    solveFrame,
  };
})();
