# Базовая модель против адаптера

Адаптер: `models/adapter_all_layers`. Генерация жадная, до 40 токенов.

## 1. Subject:
Anatomy
Topic: Urinary tract

Question:
Chronic urethral obstruction due to benign prismatic hyperplasia can lead to the following change in kidney parenchyma

Options:
0. Hyperplasia
1. Hyperophy
2. Atrophy
3. Dyplasia

**База:**

> 0. Hyperplasia

**Адаптер:**

> Answer: 0. Hyperplasia

## 2. Subject:
Microbiology
Topic: Mycology

Question:
What is the most probable poal of entry of Aspergillus?

Options:
0. Puncture wound
1. Blood
2. Lungs
3. Gastrointestinal tract

**База:**

> 0. Puncture wound

**Адаптер:**

> Answer: 2. Lungs

## 3. Subject:
Microbiology
Topic: Bacteria

Question:
Test for diagnosis of pyogenic meningitis is?

Options:
0. Widal
1. CSF PCR
2. CSF examination
3. PET scan

**База:**

> 0. Widal

**Адаптер:**

> Answer: 1. CSF PCR

## 4. Subject:
Forensic Medicine
Topic: Sexual Offenses

Question:
Which of the following condition is necessary prerequisite for this change?

Options:
0. Rupture of membranes
1. Scanty liquor amnii
2. Adequate liquor amnii
3. Air inside the uterine cavity

**База:**

> 0. Rupture of membranes

**Адаптер:**

> Answer: 2. Adequate liquor amnii

## 5. Subject:
Biochemistry
Topic: vitamins

Question:
Which of the following vitamin reduces the risk of insulin resistance, obesity, and the metabolic syndrome?

Options:
0. Vitamin A
1. Vitamin C
2. Vitamin D
3. Vitamin B12

**База:**

> 0. Vitamin A

**Адаптер:**

> Answer: 1. Vitamin C
