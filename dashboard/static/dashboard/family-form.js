const addChildButton = document.getElementById('add-child');
addChildButton.addEventListener('click', () => {
    const total = document.getElementById('id_children-TOTAL_FORMS');
    const maximum = Number(document.getElementById('id_children-MAX_NUM_FORMS').value);
    const index = Number(total.value);
    if (index >= maximum) {
        addChildButton.disabled = true;
        return;
    }
    const html = document.getElementById('empty-child-form').innerHTML.replaceAll('__prefix__', String(index));
    document.getElementById('children-forms').insertAdjacentHTML('beforeend', html);
    total.value = index + 1;
    document.getElementById('id_children-' + index + '-name').focus();
});
