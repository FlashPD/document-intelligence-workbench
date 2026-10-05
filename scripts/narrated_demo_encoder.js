(async () => {
  const canvas=document.createElement('canvas');canvas.width=1440;canvas.height=1160;
  document.body.replaceChildren(canvas);
  const ctx=canvas.getContext('2d'),audio=new AudioContext(),destination=audio.createMediaStreamDestination();
  await audio.resume();
  const stream=canvas.captureStream(30);
  for(const track of destination.stream.getAudioTracks())stream.addTrack(track);
  const mime=['video/webm;codecs=vp9,opus','video/webm;codecs=vp8,opus'].find(MediaRecorder.isTypeSupported.bind(MediaRecorder));
  if(!mime)throw Error('WebM video/audio encoder unavailable');
  const recorder=new MediaRecorder(stream,{mimeType:mime,videoBitsPerSecond:800000,audioBitsPerSecond:64000});
  const chunks=[];recorder.ondataavailable=e=>{if(e.data.size)chunks.push(e.data)};
  const stopped=new Promise((resolve,reject)=>{recorder.onstop=resolve;recorder.onerror=reject});
  let image=null,caption='Live scripted workflow',playing=false;
  function draw(){
    ctx.fillStyle='#173e2c';ctx.fillRect(0,0,1440,1160);ctx.fillStyle='white';ctx.font='24px sans-serif';ctx.fillText(caption,24,38);
    ctx.font='18px sans-serif';ctx.fillText('LIVE RULES PARSING · FICTIONAL FIXTURE · SCRIPTED ACTIONS · SYNTHESIZED NARRATION',24,77);
    ctx.fillText('Continuous viewport capture; presentation duration is not a performance or human-review measurement.',24,112);
    if(image)ctx.drawImage(image,0,160,1440,1000);
  }
  draw();const timer=setInterval(draw,1000/30);recorder.start(1000);
  window.demo={
    async frame(data){const next=new Image();next.src='data:image/png;base64,'+data;await next.decode();image=next;draw()},
    async scene(text,data){if(playing)throw Error('Narration overlap');caption=text;
      const bytes=Uint8Array.from(atob(data),c=>c.charCodeAt(0));const buffer=await audio.decodeAudioData(bytes.buffer);
      const source=audio.createBufferSource();source.buffer=buffer;source.connect(destination);playing=true;
      source.onended=()=>{playing=false;source.disconnect()};source.start();return buffer.duration},
    active(){return playing},
    async finish(){if(playing)throw Error('Narration incomplete');recorder.stop();await stopped;clearInterval(timer);
      stream.getTracks().forEach(t=>t.stop());await audio.close();
      const blob=new Blob(chunks,{type:mime});window.demoBytes=new Uint8Array(await blob.arrayBuffer());
      const video=document.createElement('video');video.muted=true;video.src=URL.createObjectURL(blob);document.body.append(video);
      await new Promise((resolve,reject)=>{video.onloadeddata=resolve;video.onerror=()=>reject(Error('Generated video cannot decode'));video.load()});
      await video.play();await new Promise(resolve=>setTimeout(resolve,500));video.pause();
      return {mime,bytes:blob.size,width:video.videoWidth,height:video.videoHeight,current_time:video.currentTime,
              audio_decoded_bytes:video.webkitAudioDecodedByteCount,video_decoded_bytes:video.webkitVideoDecodedByteCount};},
    chunk(offset,length){let binary='';const bytes=window.demoBytes.subarray(offset,offset+length);
      for(let i=0;i<bytes.length;i+=32768)binary+=String.fromCharCode(...bytes.subarray(i,i+32768));return btoa(binary)}
  };
  return {mime,width:canvas.width,height:canvas.height};
})()
