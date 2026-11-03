(() => {
  const sidebar=document.getElementById('sidebar');
  document.getElementById('menuToggle')?.addEventListener('click',()=>sidebar?.classList.toggle('open'));
  document.addEventListener('click',e=>{if(sidebar?.classList.contains('open')&&!sidebar.contains(e.target)&&!e.target.closest('#menuToggle'))sidebar.classList.remove('open')});
  // Server-authorized Kanban transition; never silently move when network denies transition.
  let moving=null;
  document.querySelectorAll('[data-opp-id]').forEach(card=>{
    card.addEventListener('dragstart',e=>{moving=card;card.classList.add('dragging'); e.dataTransfer.effectAllowed='move';e.dataTransfer.setData('text/plain',card.dataset.oppId)});
    card.addEventListener('dragend',()=>{card.classList.remove('dragging');moving=null});
  });
  document.querySelectorAll('[data-stage]').forEach(col=>{
    col.addEventListener('dragover',e=>{e.preventDefault();col.classList.add('drag-over')});
    col.addEventListener('dragleave',()=>col.classList.remove('drag-over'));
    col.addEventListener('drop',async e=>{
      e.preventDefault();col.classList.remove('drag-over');
      if(!moving)return;
      const stage=col.dataset.stage;const id=moving.dataset.oppId;
      try {
        const response=await fetch(`/api/opportunities/${encodeURIComponent(id)}/stage`,{
          method:'PATCH',headers:{'Content-Type':'application/json','X-CSRF-Token':document.querySelector('meta[name="csrf-token"]').content},
          body:JSON.stringify({stage})});
        const result=await response.json();
        if(!response.ok)throw new Error(result.error||'Falha na atualização');
        window.location.reload();
      } catch(err){alert(err.message||'Falha de rede: a etapa não foi alterada.');}
    });
  });
  // Order form: add/remove item rows (CSP allows only external scripts).
  const itemRows=document.getElementById('itemRows'),itemTpl=document.getElementById('itemTemplate');
  document.getElementById('addItem')?.addEventListener('click',()=>{if(itemRows&&itemTpl)itemRows.appendChild(itemTpl.content.cloneNode(true))});
  itemRows?.addEventListener('click',e=>{
    const btn=e.target.closest('.item-remove');if(!btn)return;
    const row=btn.closest('.item-row');
    if(itemRows.querySelectorAll('.item-row').length>1)row.remove();
    else row.querySelectorAll('input').forEach(i=>{i.value=i.name==='quantity'?'1':i.name==='unit_price'?'0.00':''});
  });
  document.querySelectorAll('[data-confirm]').forEach(form=>form.addEventListener('submit',e=>{
    if(!confirm(form.dataset.confirm))e.preventDefault();
  }));
})();
