-- Hard-tier trivia batch 3 -- 214 new rows (161 NHL, 53 PWHL), pushing
-- toward the 365-per-sport goal. Running totals after this batch:
--   NHL:  69 existing + 161 new = 230 / 365
--   PWHL: 26 existing + 53 new  = 79 / 365
-- Run this in the Supabase SQL editor -- this repo has no migration
-- tooling, same convention as the prior session_hard_trivia_batch*.sql
-- files.
--
-- Sourced from verified year-by-year Wikipedia lists: Stanley Cup
-- champions (1918-1989, filling in the pre-1990 era the first two batches
-- didn't touch), Conn Smythe Trophy, first-overall NHL draft picks, Norris
-- /Vezina/Calder Trophy winners, IIHF Women's World Championship medalists,
-- Patty Kazmaier Award winners, and CWHL Clarkson Cup / NWHL-PHF Isobel Cup
-- champions (both defunct predecessor leagues to the PWHL). Two Olympic
-- facts (2022 Beijing, 2026 Milano Cortina) are included for PWHL too.
--
-- Distractors are sampled from real entries in the same list, weighted
-- toward years close to the question's own year -- this avoids an
-- obviously-anachronistic wrong answer (e.g. a team that didn't exist yet)
-- making a question trivially easy to solve by elimination. correct_index
-- is shuffled per row, not defaulted to a fixed position.
--
-- Six Conn Smythe questions use a "won it despite the team LOSING the
-- Final" framing (1966 Crozier, 1968 Hall, 1976 Leach, 1987 Hextall, 2003
-- Giguere, 2024 McDavid) -- a real, well-documented rarity, not an error.
--
-- Validated: every options array is a 4-element JSON array with no
-- duplicate values; every correct_index is in range; no duplicate question
-- text within this batch or against any of the 95 rows already live in the
-- table (the original 2 seed rows plus the two prior curated batches); no
-- question_date collisions with existing rows. NHL new rows continue from
-- 2026-10-20 (previous batch ended 2026-10-19) through 2027-03-29. PWHL
-- new rows continue from 2026-09-07 (previous batch ended 2026-09-06)
-- through 2026-10-29.

insert into public.trivia_questions
  (question_date, tier, sport, question_text, options, correct_index, explanation, source)
values

-- ============================== NHL (161) ==============================
('2026-10-20', 'hard', 'nhl',
 'Which team won the Stanley Cup in 1918?',
 '["Vancouver Millionaires", "Victoria Cougars", "Toronto Hockey Club", "Boston Bruins"]'::jsonb, 2,
 'Toronto Hockey Club defeated Vancouver Millionaires to win the 1918 Stanley Cup.', 'curated'),

('2026-10-21', 'hard', 'nhl',
 'Which team won the Stanley Cup in 1920?',
 '["Seattle Metropolitans", "Ottawa Senators", "New York Rangers", "Montreal Maroons"]'::jsonb, 1,
 'Ottawa Senators defeated Seattle Metropolitans to win the 1920 Stanley Cup.', 'curated'),

('2026-10-22', 'hard', 'nhl',
 'Which team won the Stanley Cup in 1922?',
 '["Victoria Cougars", "Toronto St. Patricks", "Vancouver Millionaires", "Ottawa Senators"]'::jsonb, 1,
 'Toronto St. Patricks defeated Vancouver Millionaires to win the 1922 Stanley Cup.', 'curated'),

('2026-10-23', 'hard', 'nhl',
 'Which team won the Stanley Cup in 1925?',
 '["Boston Bruins", "Montreal Canadiens", "Ottawa Senators", "Victoria Cougars"]'::jsonb, 3,
 'Victoria Cougars defeated Montreal Canadiens to win the 1925 Stanley Cup.', 'curated'),

('2026-10-24', 'hard', 'nhl',
 'Which team won the Stanley Cup in 1926?',
 '["Toronto St. Patricks", "New York Rangers", "Montreal Maroons", "Victoria Cougars"]'::jsonb, 2,
 'Montreal Maroons defeated Victoria Cougars to win the 1926 Stanley Cup.', 'curated'),

('2026-10-25', 'hard', 'nhl',
 'Which team won the Stanley Cup in 1928?',
 '["Toronto St. Patricks", "New York Rangers", "Montreal Maroons", "Victoria Cougars"]'::jsonb, 1,
 'New York Rangers defeated Montreal Maroons to win the 1928 Stanley Cup.', 'curated'),

('2026-10-26', 'hard', 'nhl',
 'Which team won the Stanley Cup in 1929?',
 '["Ottawa Senators", "Boston Bruins", "Chicago Black Hawks", "New York Rangers"]'::jsonb, 1,
 'Boston Bruins defeated New York Rangers to win the 1929 Stanley Cup.', 'curated'),

('2026-10-27', 'hard', 'nhl',
 'Which team won the Stanley Cup in 1934?',
 '["New York Rangers", "Detroit Red Wings", "Chicago Black Hawks", "Montreal Canadiens"]'::jsonb, 2,
 'Chicago Black Hawks defeated Detroit Red Wings to win the 1934 Stanley Cup.', 'curated'),

('2026-10-28', 'hard', 'nhl',
 'Which team won the Stanley Cup in 1936?',
 '["New York Rangers", "Toronto Maple Leafs", "Detroit Red Wings", "Montreal Maroons"]'::jsonb, 2,
 'Detroit Red Wings defeated Toronto Maple Leafs to win the 1936 Stanley Cup.', 'curated'),

('2026-10-29', 'hard', 'nhl',
 'Which team won the Stanley Cup in 1939?',
 '["Toronto Maple Leafs", "Detroit Red Wings", "Boston Bruins", "Montreal Canadiens"]'::jsonb, 2,
 'Boston Bruins defeated Toronto Maple Leafs to win the 1939 Stanley Cup.', 'curated'),

('2026-10-30', 'hard', 'nhl',
 'Which team won the Stanley Cup in 1942?',
 '["Montreal Maroons", "New York Rangers", "Toronto Maple Leafs", "Detroit Red Wings"]'::jsonb, 2,
 'Toronto Maple Leafs defeated Detroit Red Wings to win the 1942 Stanley Cup.', 'curated'),

('2026-10-31', 'hard', 'nhl',
 'Which team won the Stanley Cup in 1944?',
 '["New York Rangers", "Montreal Canadiens", "Chicago Black Hawks", "Detroit Red Wings"]'::jsonb, 1,
 'Montreal Canadiens defeated Chicago Black Hawks to win the 1944 Stanley Cup.', 'curated'),

('2026-11-01', 'hard', 'nhl',
 'Which team won the Stanley Cup in 1950?',
 '["Boston Bruins", "New York Rangers", "Detroit Red Wings", "Toronto Maple Leafs"]'::jsonb, 2,
 'Detroit Red Wings defeated New York Rangers to win the 1950 Stanley Cup.', 'curated'),

('2026-11-02', 'hard', 'nhl',
 'Which team won the Stanley Cup in 1952?',
 '["Chicago Black Hawks", "Montreal Canadiens", "Detroit Red Wings", "Toronto Maple Leafs"]'::jsonb, 2,
 'Detroit Red Wings defeated Montreal Canadiens to win the 1952 Stanley Cup.', 'curated'),

('2026-11-03', 'hard', 'nhl',
 'Which team won the Stanley Cup in 1955?',
 '["Boston Bruins", "Montreal Canadiens", "Toronto Maple Leafs", "Detroit Red Wings"]'::jsonb, 3,
 'Detroit Red Wings defeated Montreal Canadiens to win the 1955 Stanley Cup.', 'curated'),

('2026-11-04', 'hard', 'nhl',
 'Which team won the Stanley Cup in 1961?',
 '["Montreal Canadiens", "Detroit Red Wings", "Boston Bruins", "Chicago Black Hawks"]'::jsonb, 3,
 'Chicago Black Hawks defeated Detroit Red Wings to win the 1961 Stanley Cup.', 'curated'),

('2026-11-05', 'hard', 'nhl',
 'Which team won the Stanley Cup in 1967?',
 '["Toronto Maple Leafs", "Chicago Black Hawks", "Detroit Red Wings", "Montreal Canadiens"]'::jsonb, 0,
 'Toronto Maple Leafs defeated Montreal Canadiens to win the 1967 Stanley Cup.', 'curated'),

('2026-11-06', 'hard', 'nhl',
 'Which team won the Stanley Cup in 1972?',
 '["Boston Bruins", "Toronto Maple Leafs", "New York Rangers", "Edmonton Oilers"]'::jsonb, 0,
 'Boston Bruins defeated New York Rangers to win the 1972 Stanley Cup.', 'curated'),

('2026-11-07', 'hard', 'nhl',
 'Which team won the Stanley Cup in 1974?',
 '["Boston Bruins", "Toronto Maple Leafs", "Chicago Black Hawks", "Philadelphia Flyers"]'::jsonb, 3,
 'Philadelphia Flyers defeated Boston Bruins to win the 1974 Stanley Cup.', 'curated'),

('2026-11-08', 'hard', 'nhl',
 'Which team won the Stanley Cup in 1980?',
 '["Philadelphia Flyers", "Chicago Black Hawks", "New York Islanders", "Toronto Maple Leafs"]'::jsonb, 2,
 'New York Islanders defeated Philadelphia Flyers to win the 1980 Stanley Cup.', 'curated'),

('2026-11-09', 'hard', 'nhl',
 'Which team won the Stanley Cup in 1984?',
 '["New York Islanders", "Chicago Black Hawks", "Boston Bruins", "Edmonton Oilers"]'::jsonb, 3,
 'Edmonton Oilers defeated New York Islanders to win the 1984 Stanley Cup.', 'curated'),

('2026-11-10', 'hard', 'nhl',
 'Which team won the Stanley Cup in 1989?',
 '["Philadelphia Flyers", "Toronto Maple Leafs", "Montreal Canadiens", "Calgary Flames"]'::jsonb, 3,
 'Calgary Flames defeated Montreal Canadiens to win the 1989 Stanley Cup.', 'curated'),

('2026-11-11', 'hard', 'nhl',
 'Which player won the Conn Smythe Trophy in 1965, playing for Montreal Canadiens?',
 '["Yvan Cournoyer", "Jean Beliveau", "Guy Lafleur", "Ken Dryden"]'::jsonb, 1,
 'Jean Beliveau won the Conn Smythe Trophy in 1965 with Montreal Canadiens.', 'curated'),

('2026-11-12', 'hard', 'nhl',
 'Which player won the 1966 Conn Smythe Trophy despite playing for the team that LOST the Stanley Cup Final that year?',
 '["Glenn Hall", "Dave Keon", "Bernie Parent", "Roger Crozier"]'::jsonb, 3,
 'Roger Crozier (Detroit Red Wings) won the Conn Smythe in 1966 even though Detroit Red Wings lost the Final -- one of the rare times the award has gone to a player on the losing side.', 'curated'),

('2026-11-13', 'hard', 'nhl',
 'Which player won the 1968 Conn Smythe Trophy despite playing for the team that LOST the Stanley Cup Final that year?',
 '["Glenn Hall", "Bernie Parent", "Guy Lafleur", "Jean Beliveau"]'::jsonb, 0,
 'Glenn Hall (St. Louis Blues) won the Conn Smythe in 1968 even though St. Louis Blues lost the Final -- one of the rare times the award has gone to a player on the losing side.', 'curated'),

('2026-11-14', 'hard', 'nhl',
 'Which player won the Conn Smythe Trophy in 1971, playing for Montreal Canadiens?',
 '["Ken Dryden", "Jean Beliveau", "Reggie Leach", "Yvan Cournoyer"]'::jsonb, 0,
 'Ken Dryden won the Conn Smythe Trophy in 1971 with Montreal Canadiens.', 'curated'),

('2026-11-15', 'hard', 'nhl',
 'Which player won the Conn Smythe Trophy in 1973, playing for Montreal Canadiens?',
 '["Yvan Cournoyer", "Reggie Leach", "Guy Lafleur", "Bernie Parent"]'::jsonb, 0,
 'Yvan Cournoyer won the Conn Smythe Trophy in 1973 with Montreal Canadiens.', 'curated'),

('2026-11-16', 'hard', 'nhl',
 'Which player won the Conn Smythe Trophy in 1974, playing for Philadelphia Flyers?',
 '["Serge Savard", "Bryan Trottier", "Ken Dryden", "Bernie Parent"]'::jsonb, 3,
 'Bernie Parent won the Conn Smythe Trophy in 1974 with Philadelphia Flyers.', 'curated'),

('2026-11-17', 'hard', 'nhl',
 'Which player won the Conn Smythe Trophy in 1975, playing for Philadelphia Flyers?',
 '["Bernie Parent", "Bob Gainey", "Larry Robinson", "Ken Dryden"]'::jsonb, 0,
 'Bernie Parent won the Conn Smythe Trophy in 1975 with Philadelphia Flyers.', 'curated'),

('2026-11-18', 'hard', 'nhl',
 'Which player won the Conn Smythe Trophy in 1978, playing for Montreal Canadiens?',
 '["Butch Goring", "Guy Lafleur", "Bryan Trottier", "Larry Robinson"]'::jsonb, 3,
 'Larry Robinson won the Conn Smythe Trophy in 1978 with Montreal Canadiens.', 'curated'),

('2026-11-19', 'hard', 'nhl',
 'Which player won the Conn Smythe Trophy in 1979, playing for Montreal Canadiens?',
 '["Bob Gainey", "Larry Robinson", "Reggie Leach", "Bryan Trottier"]'::jsonb, 0,
 'Bob Gainey won the Conn Smythe Trophy in 1979 with Montreal Canadiens.', 'curated'),

('2026-11-20', 'hard', 'nhl',
 'Which player won the Conn Smythe Trophy in 1980, playing for New York Islanders?',
 '["Bryan Trottier", "Yvan Cournoyer", "Bob Gainey", "Billy Smith"]'::jsonb, 0,
 'Bryan Trottier won the Conn Smythe Trophy in 1980 with New York Islanders.', 'curated'),

('2026-11-21', 'hard', 'nhl',
 'Which player won the Conn Smythe Trophy in 1981, playing for New York Islanders?',
 '["Billy Smith", "Larry Robinson", "Butch Goring", "Ron Hextall"]'::jsonb, 2,
 'Butch Goring won the Conn Smythe Trophy in 1981 with New York Islanders.', 'curated'),

('2026-11-22', 'hard', 'nhl',
 'Which player won the Conn Smythe Trophy in 1983, playing for New York Islanders?',
 '["Butch Goring", "Al MacInnis", "Bryan Trottier", "Billy Smith"]'::jsonb, 3,
 'Billy Smith won the Conn Smythe Trophy in 1983 with New York Islanders.', 'curated'),

('2026-11-23', 'hard', 'nhl',
 'Which player won the Conn Smythe Trophy in 1989, playing for Calgary Flames?',
 '["Claude Lemieux", "Al MacInnis", "Patrick Roy", "Mike Vernon"]'::jsonb, 1,
 'Al MacInnis won the Conn Smythe Trophy in 1989 with Calgary Flames.', 'curated'),

('2026-11-24', 'hard', 'nhl',
 'Which player won the Conn Smythe Trophy in 1990, playing for Edmonton Oilers?',
 '["Al MacInnis", "Joe Sakic", "Claude Lemieux", "Bill Ranford"]'::jsonb, 3,
 'Bill Ranford won the Conn Smythe Trophy in 1990 with Edmonton Oilers.', 'curated'),

('2026-11-25', 'hard', 'nhl',
 'Which player won the Conn Smythe Trophy in 1993, playing for Montreal Canadiens?',
 '["Patrick Roy", "Joe Nieuwendyk", "Claude Lemieux", "Joe Sakic"]'::jsonb, 0,
 'Patrick Roy won the Conn Smythe Trophy in 1993 with Montreal Canadiens.', 'curated'),

('2026-11-26', 'hard', 'nhl',
 'Which player won the Conn Smythe Trophy in 1995, playing for New Jersey Devils?',
 '["Ron Hextall", "Mike Vernon", "Claude Lemieux", "Joe Nieuwendyk"]'::jsonb, 2,
 'Claude Lemieux won the Conn Smythe Trophy in 1995 with New Jersey Devils.', 'curated'),

('2026-11-27', 'hard', 'nhl',
 'Which player won the Conn Smythe Trophy in 1996, playing for Colorado Avalanche?',
 '["Claude Lemieux", "Patrick Roy", "Al MacInnis", "Joe Sakic"]'::jsonb, 3,
 'Joe Sakic won the Conn Smythe Trophy in 1996 with Colorado Avalanche.', 'curated'),

('2026-11-28', 'hard', 'nhl',
 'Which player won the Conn Smythe Trophy in 1997, playing for Detroit Red Wings?',
 '["Patrick Roy", "Mike Vernon", "Brad Richards", "Bill Ranford"]'::jsonb, 1,
 'Mike Vernon won the Conn Smythe Trophy in 1997 with Detroit Red Wings.', 'curated'),

('2026-11-29', 'hard', 'nhl',
 'Which player won the Conn Smythe Trophy in 1999, playing for Dallas Stars?',
 '["Jean-Sebastien Giguere", "Joe Sakic", "Scott Stevens", "Joe Nieuwendyk"]'::jsonb, 3,
 'Joe Nieuwendyk won the Conn Smythe Trophy in 1999 with Dallas Stars.', 'curated'),

('2026-11-30', 'hard', 'nhl',
 'Which player won the Conn Smythe Trophy in 2000, playing for New Jersey Devils?',
 '["Scott Stevens", "Patrick Roy", "Claude Lemieux", "Joe Nieuwendyk"]'::jsonb, 0,
 'Scott Stevens won the Conn Smythe Trophy in 2000 with New Jersey Devils.', 'curated'),

('2026-12-01', 'hard', 'nhl',
 'Which player won the Conn Smythe Trophy in 2002, playing for Detroit Red Wings?',
 '["Cam Ward", "Joe Nieuwendyk", "Henrik Zetterberg", "Nicklas Lidstrom"]'::jsonb, 3,
 'Nicklas Lidstrom won the Conn Smythe Trophy in 2002 with Detroit Red Wings.', 'curated'),

('2026-12-02', 'hard', 'nhl',
 'Which player won the 2003 Conn Smythe Trophy despite playing for the team that LOST the Stanley Cup Final that year?',
 '["Jean-Sebastien Giguere", "Scott Niedermayer", "Joe Sakic", "Cam Ward"]'::jsonb, 0,
 'Jean-Sebastien Giguere (Mighty Ducks of Anaheim) won the Conn Smythe in 2003 even though Mighty Ducks of Anaheim lost the Final -- one of the rare times the award has gone to a player on the losing side.', 'curated'),

('2026-12-03', 'hard', 'nhl',
 'Which player won the Conn Smythe Trophy in 2004, playing for Tampa Bay Lightning?',
 '["Henrik Zetterberg", "Scott Niedermayer", "Brad Richards", "Nicklas Lidstrom"]'::jsonb, 2,
 'Brad Richards won the Conn Smythe Trophy in 2004 with Tampa Bay Lightning.', 'curated'),

('2026-12-04', 'hard', 'nhl',
 'Which player won the Conn Smythe Trophy in 2006, playing for Carolina Hurricanes?',
 '["Cam Ward", "Tim Thomas", "Henrik Zetterberg", "Scott Stevens"]'::jsonb, 0,
 'Cam Ward won the Conn Smythe Trophy in 2006 with Carolina Hurricanes.', 'curated'),

('2026-12-05', 'hard', 'nhl',
 'Which player won the Conn Smythe Trophy in 2007, playing for Anaheim Ducks?',
 '["Nicklas Lidstrom", "Scott Niedermayer", "Jonathan Toews", "Cam Ward"]'::jsonb, 1,
 'Scott Niedermayer won the Conn Smythe Trophy in 2007 with Anaheim Ducks.', 'curated'),

('2026-12-06', 'hard', 'nhl',
 'Which player won the Conn Smythe Trophy in 2012, playing for Los Angeles Kings?',
 '["Henrik Zetterberg", "Jonathan Quick", "Ryan O''Reilly", "Scott Niedermayer"]'::jsonb, 1,
 'Jonathan Quick won the Conn Smythe Trophy in 2012 with Los Angeles Kings.', 'curated'),

('2026-12-07', 'hard', 'nhl',
 'Which player won the Conn Smythe Trophy in 2013, playing for Chicago Blackhawks?',
 '["Patrick Kane", "Cam Ward", "Jonathan Toews", "Jonathan Quick"]'::jsonb, 0,
 'Patrick Kane won the Conn Smythe Trophy in 2013 with Chicago Blackhawks.', 'curated'),

('2026-12-08', 'hard', 'nhl',
 'Which player won the Conn Smythe Trophy in 2021, playing for Tampa Bay Lightning?',
 '["Ryan O''Reilly", "Andrei Vasilevskiy", "Jonathan Marchessault", "Jonathan Quick"]'::jsonb, 1,
 'Andrei Vasilevskiy won the Conn Smythe Trophy in 2021 with Tampa Bay Lightning.', 'curated'),

('2026-12-09', 'hard', 'nhl',
 'Which player won the Conn Smythe Trophy in 2023, playing for Vegas Golden Knights?',
 '["Victor Hedman", "Jonathan Marchessault", "Duncan Keith", "Tim Thomas"]'::jsonb, 1,
 'Jonathan Marchessault won the Conn Smythe Trophy in 2023 with Vegas Golden Knights.', 'curated'),

('2026-12-10', 'hard', 'nhl',
 'Which player won the 2024 Conn Smythe Trophy despite playing for the team that LOST the Stanley Cup Final that year?',
 '["Jonathan Marchessault", "Connor McDavid", "Tim Thomas", "Jonathan Quick"]'::jsonb, 1,
 'Connor McDavid (Edmonton Oilers) won the Conn Smythe in 2024 even though Edmonton Oilers lost the Final -- one of the rare times the award has gone to a player on the losing side.', 'curated'),

('2026-12-11', 'hard', 'nhl',
 'Who was selected first overall by New York Rangers in the 1965 NHL Entry Draft?',
 '["Gilbert Perreault", "Barry Gibbs", "Michel Plasse", "Andre Veilleux"]'::jsonb, 3,
 'Andre Veilleux was the first overall pick of the 1965 NHL Entry Draft, selected by New York Rangers.', 'curated'),

('2026-12-12', 'hard', 'nhl',
 'Who was selected first overall by Boston Bruins in the 1966 NHL Entry Draft?',
 '["Michel Plasse", "Billy Harris", "Barry Gibbs", "Rejean Houle"]'::jsonb, 2,
 'Barry Gibbs was the first overall pick of the 1966 NHL Entry Draft, selected by Boston Bruins.', 'curated'),

('2026-12-13', 'hard', 'nhl',
 'Who was selected first overall by Los Angeles Kings in the 1967 NHL Entry Draft?',
 '["Rick Pagnutti", "Barry Gibbs", "Mel Bridgman", "Greg Joly"]'::jsonb, 0,
 'Rick Pagnutti was the first overall pick of the 1967 NHL Entry Draft, selected by Los Angeles Kings.', 'curated'),

('2026-12-14', 'hard', 'nhl',
 'Who was selected first overall by Montreal Canadiens in the 1968 NHL Entry Draft?',
 '["Mel Bridgman", "Billy Harris", "Gilbert Perreault", "Michel Plasse"]'::jsonb, 3,
 'Michel Plasse was the first overall pick of the 1968 NHL Entry Draft, selected by Montreal Canadiens.', 'curated'),

('2026-12-15', 'hard', 'nhl',
 'Who was selected first overall by Montreal Canadiens in the 1969 NHL Entry Draft?',
 '["Gilbert Perreault", "Greg Joly", "Claude Gauthier", "Rejean Houle"]'::jsonb, 3,
 'Rejean Houle was the first overall pick of the 1969 NHL Entry Draft, selected by Montreal Canadiens.', 'curated'),

('2026-12-16', 'hard', 'nhl',
 'Who was selected first overall by Buffalo Sabres in the 1970 NHL Entry Draft?',
 '["Gilbert Perreault", "Rick Pagnutti", "Claude Gauthier", "Michel Plasse"]'::jsonb, 0,
 'Gilbert Perreault was the first overall pick of the 1970 NHL Entry Draft, selected by Buffalo Sabres.', 'curated'),

('2026-12-17', 'hard', 'nhl',
 'Who was selected first overall by New York Islanders in the 1972 NHL Entry Draft?',
 '["Mel Bridgman", "Gilbert Perreault", "Billy Harris", "Dale McCourt"]'::jsonb, 2,
 'Billy Harris was the first overall pick of the 1972 NHL Entry Draft, selected by New York Islanders.', 'curated'),

('2026-12-18', 'hard', 'nhl',
 'Who was selected first overall by Washington Capitals in the 1974 NHL Entry Draft?',
 '["Rob Ramage", "Greg Joly", "Billy Harris", "Bobby Smith"]'::jsonb, 1,
 'Greg Joly was the first overall pick of the 1974 NHL Entry Draft, selected by Washington Capitals.', 'curated'),

('2026-12-19', 'hard', 'nhl',
 'Who was selected first overall by Colorado Rockies in the 1979 NHL Entry Draft?',
 '["Rick Green", "Greg Joly", "Doug Wickenheiser", "Rob Ramage"]'::jsonb, 3,
 'Rob Ramage was the first overall pick of the 1979 NHL Entry Draft, selected by Colorado Rockies.', 'curated'),

('2026-12-20', 'hard', 'nhl',
 'Who was selected first overall by Montreal Canadiens in the 1980 NHL Entry Draft?',
 '["Greg Joly", "Bobby Smith", "Doug Wickenheiser", "Rob Ramage"]'::jsonb, 2,
 'Doug Wickenheiser was the first overall pick of the 1980 NHL Entry Draft, selected by Montreal Canadiens.', 'curated'),

('2026-12-21', 'hard', 'nhl',
 'Who was selected first overall by Boston Bruins in the 1982 NHL Entry Draft?',
 '["Bobby Smith", "Rob Ramage", "Dale McCourt", "Gord Kluzak"]'::jsonb, 3,
 'Gord Kluzak was the first overall pick of the 1982 NHL Entry Draft, selected by Boston Bruins.', 'curated'),

('2026-12-22', 'hard', 'nhl',
 'Who was selected first overall by Minnesota North Stars in the 1983 NHL Entry Draft?',
 '["Doug Wickenheiser", "Dale McCourt", "Brian Lawton", "Bobby Smith"]'::jsonb, 2,
 'Brian Lawton was the first overall pick of the 1983 NHL Entry Draft, selected by Minnesota North Stars.', 'curated'),

('2026-12-23', 'hard', 'nhl',
 'Who was selected first overall by Toronto Maple Leafs in the 1985 NHL Entry Draft?',
 '["Wendel Clark", "Bobby Smith", "Joe Murphy", "Doug Wickenheiser"]'::jsonb, 0,
 'Wendel Clark was the first overall pick of the 1985 NHL Entry Draft, selected by Toronto Maple Leafs.', 'curated'),

('2026-12-24', 'hard', 'nhl',
 'Who was selected first overall by Detroit Red Wings in the 1986 NHL Entry Draft?',
 '["Bobby Smith", "Rob Ramage", "Brian Lawton", "Joe Murphy"]'::jsonb, 3,
 'Joe Murphy was the first overall pick of the 1986 NHL Entry Draft, selected by Detroit Red Wings.', 'curated'),

('2026-12-25', 'hard', 'nhl',
 'Who was selected first overall by Buffalo Sabres in the 1987 NHL Entry Draft?',
 '["Roman Hamrlik", "Gord Kluzak", "Rob Ramage", "Pierre Turgeon"]'::jsonb, 3,
 'Pierre Turgeon was the first overall pick of the 1987 NHL Entry Draft, selected by Buffalo Sabres.', 'curated'),

('2026-12-26', 'hard', 'nhl',
 'Who was selected first overall by Quebec Nordiques in the 1990 NHL Entry Draft?',
 '["Gord Kluzak", "Pierre Turgeon", "Wendel Clark", "Owen Nolan"]'::jsonb, 3,
 'Owen Nolan was the first overall pick of the 1990 NHL Entry Draft, selected by Quebec Nordiques.', 'curated'),

('2026-12-27', 'hard', 'nhl',
 'Who was selected first overall by Tampa Bay Lightning in the 1992 NHL Entry Draft?',
 '["Chris Phillips", "Roman Hamrlik", "Patrik Stefan", "Vincent Lecavalier"]'::jsonb, 1,
 'Roman Hamrlik was the first overall pick of the 1992 NHL Entry Draft, selected by Tampa Bay Lightning.', 'curated'),

('2026-12-28', 'hard', 'nhl',
 'Who was selected first overall by Florida Panthers in the 1994 NHL Entry Draft?',
 '["Chris Phillips", "Ed Jovanovski", "Pierre Turgeon", "Ilya Kovalchuk"]'::jsonb, 1,
 'Ed Jovanovski was the first overall pick of the 1994 NHL Entry Draft, selected by Florida Panthers.', 'curated'),

('2026-12-29', 'hard', 'nhl',
 'Who was selected first overall by Ottawa Senators in the 1995 NHL Entry Draft?',
 '["Bryan Berard", "Rick DiPietro", "Chris Phillips", "Rick Nash"]'::jsonb, 0,
 'Bryan Berard was the first overall pick of the 1995 NHL Entry Draft, selected by Ottawa Senators.', 'curated'),

('2026-12-30', 'hard', 'nhl',
 'Who was selected first overall by Ottawa Senators in the 1996 NHL Entry Draft?',
 '["Chris Phillips", "Rick DiPietro", "Ed Jovanovski", "Rick Nash"]'::jsonb, 0,
 'Chris Phillips was the first overall pick of the 1996 NHL Entry Draft, selected by Ottawa Senators.', 'curated'),

('2026-12-31', 'hard', 'nhl',
 'Who was selected first overall by Tampa Bay Lightning in the 1998 NHL Entry Draft?',
 '["Vincent Lecavalier", "Roman Hamrlik", "Bryan Berard", "Marc-Andre Fleury"]'::jsonb, 0,
 'Vincent Lecavalier was the first overall pick of the 1998 NHL Entry Draft, selected by Tampa Bay Lightning.', 'curated'),

('2027-01-01', 'hard', 'nhl',
 'Who was selected first overall by New York Islanders in the 2000 NHL Entry Draft?',
 '["Ilya Kovalchuk", "Ed Jovanovski", "Rick DiPietro", "Patrik Stefan"]'::jsonb, 2,
 'Rick DiPietro was the first overall pick of the 2000 NHL Entry Draft, selected by New York Islanders.', 'curated'),

('2027-01-02', 'hard', 'nhl',
 'Who was selected first overall by Columbus Blue Jackets in the 2002 NHL Entry Draft?',
 '["Chris Phillips", "Marc-Andre Fleury", "Steven Stamkos", "Rick Nash"]'::jsonb, 3,
 'Rick Nash was the first overall pick of the 2002 NHL Entry Draft, selected by Columbus Blue Jackets.', 'curated'),

('2027-01-03', 'hard', 'nhl',
 'Who was selected first overall by Pittsburgh Penguins in the 2003 NHL Entry Draft?',
 '["Patrik Stefan", "Ilya Kovalchuk", "Vincent Lecavalier", "Marc-Andre Fleury"]'::jsonb, 3,
 'Marc-Andre Fleury was the first overall pick of the 2003 NHL Entry Draft, selected by Pittsburgh Penguins.', 'curated'),

('2027-01-04', 'hard', 'nhl',
 'Who was selected first overall by St. Louis Blues in the 2006 NHL Entry Draft?',
 '["Rick DiPietro", "Erik Johnson", "Marc-Andre Fleury", "Steven Stamkos"]'::jsonb, 1,
 'Erik Johnson was the first overall pick of the 2006 NHL Entry Draft, selected by St. Louis Blues.', 'curated'),

('2027-01-05', 'hard', 'nhl',
 'Who was selected first overall by Chicago Blackhawks in the 2007 NHL Entry Draft?',
 '["John Tavares", "Ryan Nugent-Hopkins", "Patrick Kane", "Taylor Hall"]'::jsonb, 2,
 'Patrick Kane was the first overall pick of the 2007 NHL Entry Draft, selected by Chicago Blackhawks.', 'curated'),

('2027-01-06', 'hard', 'nhl',
 'Who was selected first overall by Tampa Bay Lightning in the 2008 NHL Entry Draft?',
 '["Steven Stamkos", "Ryan Nugent-Hopkins", "John Tavares", "Rick Nash"]'::jsonb, 0,
 'Steven Stamkos was the first overall pick of the 2008 NHL Entry Draft, selected by Tampa Bay Lightning.', 'curated'),

('2027-01-07', 'hard', 'nhl',
 'Who was selected first overall by Edmonton Oilers in the 2010 NHL Entry Draft?',
 '["Taylor Hall", "Auston Matthews", "Patrick Kane", "Steven Stamkos"]'::jsonb, 0,
 'Taylor Hall was the first overall pick of the 2010 NHL Entry Draft, selected by Edmonton Oilers.', 'curated'),

('2027-01-08', 'hard', 'nhl',
 'Who was selected first overall by Edmonton Oilers in the 2011 NHL Entry Draft?',
 '["John Tavares", "Aaron Ekblad", "Auston Matthews", "Ryan Nugent-Hopkins"]'::jsonb, 3,
 'Ryan Nugent-Hopkins was the first overall pick of the 2011 NHL Entry Draft, selected by Edmonton Oilers.', 'curated'),

('2027-01-09', 'hard', 'nhl',
 'Who was selected first overall by Edmonton Oilers in the 2012 NHL Entry Draft?',
 '["Ryan Nugent-Hopkins", "Patrick Kane", "Taylor Hall", "Nail Yakupov"]'::jsonb, 3,
 'Nail Yakupov was the first overall pick of the 2012 NHL Entry Draft, selected by Edmonton Oilers.', 'curated'),

('2027-01-10', 'hard', 'nhl',
 'Who was selected first overall by Toronto Maple Leafs in the 2016 NHL Entry Draft?',
 '["Jack Hughes", "Auston Matthews", "Rasmus Dahlin", "Nail Yakupov"]'::jsonb, 1,
 'Auston Matthews was the first overall pick of the 2016 NHL Entry Draft, selected by Toronto Maple Leafs.', 'curated'),

('2027-01-11', 'hard', 'nhl',
 'Who was selected first overall by New Jersey Devils in the 2017 NHL Entry Draft?',
 '["Auston Matthews", "Nico Hischier", "Nathan MacKinnon", "Aaron Ekblad"]'::jsonb, 1,
 'Nico Hischier was the first overall pick of the 2017 NHL Entry Draft, selected by New Jersey Devils.', 'curated'),

('2027-01-12', 'hard', 'nhl',
 'Who was selected first overall by Buffalo Sabres in the 2018 NHL Entry Draft?',
 '["Auston Matthews", "Rasmus Dahlin", "Nail Yakupov", "Jack Hughes"]'::jsonb, 1,
 'Rasmus Dahlin was the first overall pick of the 2018 NHL Entry Draft, selected by Buffalo Sabres.', 'curated'),

('2027-01-13', 'hard', 'nhl',
 'Who was selected first overall by Montreal Canadiens in the 2022 NHL Entry Draft?',
 '["Juraj Slafkovsky", "Nico Hischier", "Jack Hughes", "Matthew Schaefer"]'::jsonb, 0,
 'Juraj Slafkovsky was the first overall pick of the 2022 NHL Entry Draft, selected by Montreal Canadiens.', 'curated'),

('2027-01-14', 'hard', 'nhl',
 'Who was selected first overall by New York Islanders in the 2025 NHL Entry Draft?',
 '["Nico Hischier", "Matthew Schaefer", "Rasmus Dahlin", "Aaron Ekblad"]'::jsonb, 1,
 'Matthew Schaefer was the first overall pick of the 2025 NHL Entry Draft, selected by New York Islanders.', 'curated'),

('2027-01-15', 'hard', 'nhl',
 'Who won the Norris Trophy as the NHL''s best defenseman in 1962, playing for New York Rangers?',
 '["Randy Carlyle", "Doug Wilson", "Rod Langway", "Doug Harvey"]'::jsonb, 3,
 'Doug Harvey won the Norris Trophy in 1962 with New York Rangers.', 'curated'),

('2027-01-16', 'hard', 'nhl',
 'Who won the Norris Trophy as the NHL''s best defenseman in 1963, playing for Chicago Black Hawks?',
 '["Denis Potvin", "Jacques Laperriere", "Pierre Pilote", "Doug Harvey"]'::jsonb, 2,
 'Pierre Pilote won the Norris Trophy in 1963 with Chicago Black Hawks.', 'curated'),

('2027-01-17', 'hard', 'nhl',
 'Who won the Norris Trophy as the NHL''s best defenseman in 1967, playing for New York Rangers?',
 '["Jacques Laperriere", "Larry Robinson", "Denis Potvin", "Harry Howell"]'::jsonb, 3,
 'Harry Howell won the Norris Trophy in 1967 with New York Rangers.', 'curated'),

('2027-01-18', 'hard', 'nhl',
 'Who won the Norris Trophy as the NHL''s best defenseman in 1968, playing for Boston Bruins?',
 '["Doug Wilson", "Jacques Laperriere", "Pierre Pilote", "Bobby Orr"]'::jsonb, 3,
 'Bobby Orr won the Norris Trophy in 1968 with Boston Bruins.', 'curated'),

('2027-01-19', 'hard', 'nhl',
 'Who won the Norris Trophy as the NHL''s best defenseman in 1976, playing for New York Islanders?',
 '["Paul Coffey", "Bobby Orr", "Denis Potvin", "Larry Robinson"]'::jsonb, 2,
 'Denis Potvin won the Norris Trophy in 1976 with New York Islanders.', 'curated'),

('2027-01-20', 'hard', 'nhl',
 'Who won the Norris Trophy as the NHL''s best defenseman in 1977, playing for Montreal Canadiens?',
 '["Denis Potvin", "Paul Coffey", "Bobby Orr", "Larry Robinson"]'::jsonb, 3,
 'Larry Robinson won the Norris Trophy in 1977 with Montreal Canadiens.', 'curated'),

('2027-01-21', 'hard', 'nhl',
 'Who won the Norris Trophy as the NHL''s best defenseman in 1981, playing for Pittsburgh Penguins?',
 '["Chris Chelios", "Denis Potvin", "Larry Robinson", "Randy Carlyle"]'::jsonb, 3,
 'Randy Carlyle won the Norris Trophy in 1981 with Pittsburgh Penguins.', 'curated'),

('2027-01-22', 'hard', 'nhl',
 'Who won the Norris Trophy as the NHL''s best defenseman in 1982, playing for Chicago Black Hawks?',
 '["Brian Leetch", "Rod Langway", "Denis Potvin", "Doug Wilson"]'::jsonb, 3,
 'Doug Wilson won the Norris Trophy in 1982 with Chicago Black Hawks.', 'curated'),

('2027-01-23', 'hard', 'nhl',
 'Who won the Norris Trophy as the NHL''s best defenseman in 1983, playing for Washington Capitals?',
 '["Paul Coffey", "Chris Chelios", "Rod Langway", "Randy Carlyle"]'::jsonb, 2,
 'Rod Langway won the Norris Trophy in 1983 with Washington Capitals.', 'curated'),

('2027-01-24', 'hard', 'nhl',
 'Who won the Norris Trophy as the NHL''s best defenseman in 1985, playing for Edmonton Oilers?',
 '["Rob Blake", "Brian Leetch", "Paul Coffey", "Al MacInnis"]'::jsonb, 2,
 'Paul Coffey won the Norris Trophy in 1985 with Edmonton Oilers.', 'curated'),

('2027-01-25', 'hard', 'nhl',
 'Who won the Norris Trophy as the NHL''s best defenseman in 1989, playing for Montreal Canadiens?',
 '["Chris Pronger", "Rod Langway", "Paul Coffey", "Chris Chelios"]'::jsonb, 3,
 'Chris Chelios won the Norris Trophy in 1989 with Montreal Canadiens.', 'curated'),

('2027-01-26', 'hard', 'nhl',
 'Who won the Norris Trophy as the NHL''s best defenseman in 1992, playing for New York Rangers?',
 '["Rob Blake", "Brian Leetch", "Paul Coffey", "Chris Chelios"]'::jsonb, 1,
 'Brian Leetch won the Norris Trophy in 1992 with New York Rangers.', 'curated'),

('2027-01-27', 'hard', 'nhl',
 'Who won the Norris Trophy as the NHL''s best defenseman in 1995, playing for Detroit Red Wings?',
 '["Al MacInnis", "Paul Coffey", "Randy Carlyle", "Doug Wilson"]'::jsonb, 1,
 'Paul Coffey won the Norris Trophy in 1995 with Detroit Red Wings.', 'curated'),

('2027-01-28', 'hard', 'nhl',
 'Who won the Norris Trophy as the NHL''s best defenseman in 1998, playing for Los Angeles Kings?',
 '["Rob Blake", "Brian Leetch", "Zdeno Chara", "Scott Niedermayer"]'::jsonb, 0,
 'Rob Blake won the Norris Trophy in 1998 with Los Angeles Kings.', 'curated'),

('2027-01-29', 'hard', 'nhl',
 'Who won the Norris Trophy as the NHL''s best defenseman in 1999, playing for St. Louis Blues?',
 '["Al MacInnis", "Scott Niedermayer", "Duncan Keith", "Erik Karlsson"]'::jsonb, 0,
 'Al MacInnis won the Norris Trophy in 1999 with St. Louis Blues.', 'curated'),

('2027-01-30', 'hard', 'nhl',
 'Who won the Norris Trophy as the NHL''s best defenseman in 2004, playing for New Jersey Devils?',
 '["Paul Coffey", "Scott Niedermayer", "P.K. Subban", "Rob Blake"]'::jsonb, 1,
 'Scott Niedermayer won the Norris Trophy in 2004 with New Jersey Devils.', 'curated'),

('2027-01-31', 'hard', 'nhl',
 'Who won the Norris Trophy as the NHL''s best defenseman in 2009, playing for Boston Bruins?',
 '["Zdeno Chara", "Scott Niedermayer", "Erik Karlsson", "Chris Pronger"]'::jsonb, 0,
 'Zdeno Chara won the Norris Trophy in 2009 with Boston Bruins.', 'curated'),

('2027-02-01', 'hard', 'nhl',
 'Who won the Norris Trophy as the NHL''s best defenseman in 2010, playing for Chicago Blackhawks?',
 '["Zdeno Chara", "P.K. Subban", "Duncan Keith", "Scott Niedermayer"]'::jsonb, 2,
 'Duncan Keith won the Norris Trophy in 2010 with Chicago Blackhawks.', 'curated'),

('2027-02-02', 'hard', 'nhl',
 'Who won the Norris Trophy as the NHL''s best defenseman in 2012, playing for Ottawa Senators?',
 '["Erik Karlsson", "Brent Burns", "Scott Niedermayer", "Drew Doughty"]'::jsonb, 0,
 'Erik Karlsson won the Norris Trophy in 2012 with Ottawa Senators.', 'curated'),

('2027-02-03', 'hard', 'nhl',
 'Who won the Norris Trophy as the NHL''s best defenseman in 2013, playing for Montreal Canadiens?',
 '["Brent Burns", "Erik Karlsson", "Victor Hedman", "P.K. Subban"]'::jsonb, 3,
 'P.K. Subban won the Norris Trophy in 2013 with Montreal Canadiens.', 'curated'),

('2027-02-04', 'hard', 'nhl',
 'Who won the Norris Trophy as the NHL''s best defenseman in 2016, playing for Los Angeles Kings?',
 '["P.K. Subban", "Brent Burns", "Zdeno Chara", "Drew Doughty"]'::jsonb, 3,
 'Drew Doughty won the Norris Trophy in 2016 with Los Angeles Kings.', 'curated'),

('2027-02-05', 'hard', 'nhl',
 'Who won the Norris Trophy as the NHL''s best defenseman in 2017, playing for San Jose Sharks?',
 '["Brent Burns", "Roman Josi", "Adam Fox", "P.K. Subban"]'::jsonb, 0,
 'Brent Burns won the Norris Trophy in 2017 with San Jose Sharks.', 'curated'),

('2027-02-06', 'hard', 'nhl',
 'Who won the Norris Trophy as the NHL''s best defenseman in 2018, playing for Tampa Bay Lightning?',
 '["Brent Burns", "Victor Hedman", "Drew Doughty", "Roman Josi"]'::jsonb, 1,
 'Victor Hedman won the Norris Trophy in 2018 with Tampa Bay Lightning.', 'curated'),

('2027-02-07', 'hard', 'nhl',
 'Who won the Norris Trophy as the NHL''s best defenseman in 2019, playing for Calgary Flames?',
 '["Drew Doughty", "P.K. Subban", "Mark Giordano", "Adam Fox"]'::jsonb, 2,
 'Mark Giordano won the Norris Trophy in 2019 with Calgary Flames.', 'curated'),

('2027-02-08', 'hard', 'nhl',
 'Who won the Norris Trophy as the NHL''s best defenseman in 2020, playing for Nashville Predators?',
 '["Roman Josi", "Victor Hedman", "Mark Giordano", "Quinn Hughes"]'::jsonb, 0,
 'Roman Josi won the Norris Trophy in 2020 with Nashville Predators.', 'curated'),

('2027-02-09', 'hard', 'nhl',
 'Who won the Norris Trophy as the NHL''s best defenseman in 2021, playing for New York Rangers?',
 '["Adam Fox", "Brent Burns", "Cale Makar", "Mark Giordano"]'::jsonb, 0,
 'Adam Fox won the Norris Trophy in 2021 with New York Rangers.', 'curated'),

('2027-02-10', 'hard', 'nhl',
 'Who won the Norris Trophy as the NHL''s best defenseman in 2024, playing for Vancouver Canucks?',
 '["Adam Fox", "Quinn Hughes", "P.K. Subban", "Roman Josi"]'::jsonb, 1,
 'Quinn Hughes won the Norris Trophy in 2024 with Vancouver Canucks.', 'curated'),

('2027-02-11', 'hard', 'nhl',
 'Who won the Norris Trophy as the NHL''s best defenseman in 2026, playing for Columbus Blue Jackets?',
 '["Drew Doughty", "Quinn Hughes", "Roman Josi", "Zach Werenski"]'::jsonb, 3,
 'Zach Werenski won the Norris Trophy in 2026 with Columbus Blue Jackets.', 'curated'),

('2027-02-12', 'hard', 'nhl',
 'Who won the Vezina Trophy as the NHL''s best goaltender in 1982, playing for New York Islanders?',
 '["Ed Belfour", "Billy Smith", "Pelle Lindbergh", "Tom Barrasso"]'::jsonb, 1,
 'Billy Smith won the Vezina Trophy in 1982 with New York Islanders.', 'curated'),

('2027-02-13', 'hard', 'nhl',
 'Who won the Vezina Trophy as the NHL''s best goaltender in 1983, playing for Boston Bruins?',
 '["Pete Peeters", "Dominik Hasek", "Patrick Roy", "Ed Belfour"]'::jsonb, 0,
 'Pete Peeters won the Vezina Trophy in 1983 with Boston Bruins.', 'curated'),

('2027-02-14', 'hard', 'nhl',
 'Who won the Vezina Trophy as the NHL''s best goaltender in 1984, playing for Buffalo Sabres?',
 '["Pelle Lindbergh", "Patrick Roy", "Tom Barrasso", "Ed Belfour"]'::jsonb, 2,
 'Tom Barrasso won the Vezina Trophy in 1984 with Buffalo Sabres.', 'curated'),

('2027-02-15', 'hard', 'nhl',
 'Who won the Vezina Trophy as the NHL''s best goaltender in 1985, playing for Philadelphia Flyers?',
 '["Patrick Roy", "Dominik Hasek", "Pelle Lindbergh", "Grant Fuhr"]'::jsonb, 2,
 'Pelle Lindbergh won the Vezina Trophy in 1985 with Philadelphia Flyers.', 'curated'),

('2027-02-16', 'hard', 'nhl',
 'Who won the Vezina Trophy as the NHL''s best goaltender in 1986, playing for New York Rangers?',
 '["Pelle Lindbergh", "Grant Fuhr", "John Vanbiesbrouck", "Ed Belfour"]'::jsonb, 2,
 'John Vanbiesbrouck won the Vezina Trophy in 1986 with New York Rangers.', 'curated'),

('2027-02-17', 'hard', 'nhl',
 'Who won the Vezina Trophy as the NHL''s best goaltender in 1987, playing for Philadelphia Flyers?',
 '["Ron Hextall", "Billy Smith", "Ed Belfour", "Grant Fuhr"]'::jsonb, 0,
 'Ron Hextall won the Vezina Trophy in 1987 with Philadelphia Flyers.', 'curated'),

('2027-02-18', 'hard', 'nhl',
 'Who won the Vezina Trophy as the NHL''s best goaltender in 1988, playing for Edmonton Oilers?',
 '["Dominik Hasek", "John Vanbiesbrouck", "Grant Fuhr", "Pete Peeters"]'::jsonb, 2,
 'Grant Fuhr won the Vezina Trophy in 1988 with Edmonton Oilers.', 'curated'),

('2027-02-19', 'hard', 'nhl',
 'Who won the Vezina Trophy as the NHL''s best goaltender in 1991, playing for Chicago Blackhawks?',
 '["Ed Belfour", "Grant Fuhr", "Pete Peeters", "Pelle Lindbergh"]'::jsonb, 0,
 'Ed Belfour won the Vezina Trophy in 1991 with Chicago Blackhawks.', 'curated'),

('2027-02-20', 'hard', 'nhl',
 'Who won the Vezina Trophy as the NHL''s best goaltender in 1993, playing for Chicago Blackhawks?',
 '["Tom Barrasso", "Ed Belfour", "Jose Theodore", "Pete Peeters"]'::jsonb, 1,
 'Ed Belfour won the Vezina Trophy in 1993 with Chicago Blackhawks.', 'curated'),

('2027-02-21', 'hard', 'nhl',
 'Who won the Vezina Trophy as the NHL''s best goaltender in 1994, playing for Buffalo Sabres?',
 '["Pelle Lindbergh", "John Vanbiesbrouck", "Dominik Hasek", "Grant Fuhr"]'::jsonb, 2,
 'Dominik Hasek won the Vezina Trophy in 1994 with Buffalo Sabres.', 'curated'),

('2027-02-22', 'hard', 'nhl',
 'Who won the Vezina Trophy as the NHL''s best goaltender in 2002, playing for Montreal Canadiens?',
 '["Henrik Lundqvist", "Jose Theodore", "Patrick Roy", "Sergei Bobrovsky"]'::jsonb, 1,
 'Jose Theodore won the Vezina Trophy in 2002 with Montreal Canadiens.', 'curated'),

('2027-02-23', 'hard', 'nhl',
 'Who won the Vezina Trophy as the NHL''s best goaltender in 2006, playing for Calgary Flames?',
 '["Jose Theodore", "Miikka Kiprusoff", "Sergei Bobrovsky", "Ryan Miller"]'::jsonb, 1,
 'Miikka Kiprusoff won the Vezina Trophy in 2006 with Calgary Flames.', 'curated'),

('2027-02-24', 'hard', 'nhl',
 'Who won the Vezina Trophy as the NHL''s best goaltender in 2009, playing for Boston Bruins?',
 '["Pekka Rinne", "Sergei Bobrovsky", "Tuukka Rask", "Tim Thomas"]'::jsonb, 3,
 'Tim Thomas won the Vezina Trophy in 2009 with Boston Bruins.', 'curated'),

('2027-02-25', 'hard', 'nhl',
 'Who won the Vezina Trophy as the NHL''s best goaltender in 2010, playing for Buffalo Sabres?',
 '["Ryan Miller", "Braden Holtby", "Miikka Kiprusoff", "Tuukka Rask"]'::jsonb, 0,
 'Ryan Miller won the Vezina Trophy in 2010 with Buffalo Sabres.', 'curated'),

('2027-02-26', 'hard', 'nhl',
 'Who won the Vezina Trophy as the NHL''s best goaltender in 2013, playing for Columbus Blue Jackets?',
 '["Braden Holtby", "Andrei Vasilevskiy", "Ryan Miller", "Sergei Bobrovsky"]'::jsonb, 3,
 'Sergei Bobrovsky won the Vezina Trophy in 2013 with Columbus Blue Jackets.', 'curated'),

('2027-02-27', 'hard', 'nhl',
 'Who won the Vezina Trophy as the NHL''s best goaltender in 2014, playing for Boston Bruins?',
 '["Tuukka Rask", "Tim Thomas", "Carey Price", "Andrei Vasilevskiy"]'::jsonb, 0,
 'Tuukka Rask won the Vezina Trophy in 2014 with Boston Bruins.', 'curated'),

('2027-02-28', 'hard', 'nhl',
 'Who won the Vezina Trophy as the NHL''s best goaltender in 2015, playing for Montreal Canadiens?',
 '["Pekka Rinne", "Carey Price", "Braden Holtby", "Connor Hellebuyck"]'::jsonb, 1,
 'Carey Price won the Vezina Trophy in 2015 with Montreal Canadiens.', 'curated'),

('2027-03-01', 'hard', 'nhl',
 'Who won the Vezina Trophy as the NHL''s best goaltender in 2016, playing for Washington Capitals?',
 '["Sergei Bobrovsky", "Tuukka Rask", "Braden Holtby", "Connor Hellebuyck"]'::jsonb, 2,
 'Braden Holtby won the Vezina Trophy in 2016 with Washington Capitals.', 'curated'),

('2027-03-02', 'hard', 'nhl',
 'Who won the Vezina Trophy as the NHL''s best goaltender in 2018, playing for Nashville Predators?',
 '["Pekka Rinne", "Linus Ullmark", "Connor Hellebuyck", "Marc-Andre Fleury"]'::jsonb, 0,
 'Pekka Rinne won the Vezina Trophy in 2018 with Nashville Predators.', 'curated'),

('2027-03-03', 'hard', 'nhl',
 'Who won the Vezina Trophy as the NHL''s best goaltender in 2019, playing for Tampa Bay Lightning?',
 '["Tuukka Rask", "Sergei Bobrovsky", "Andrei Vasilevskiy", "Igor Shesterkin"]'::jsonb, 2,
 'Andrei Vasilevskiy won the Vezina Trophy in 2019 with Tampa Bay Lightning.', 'curated'),

('2027-03-04', 'hard', 'nhl',
 'Who won the Vezina Trophy as the NHL''s best goaltender in 2021, playing for Vegas Golden Knights?',
 '["Marc-Andre Fleury", "Sergei Bobrovsky", "Carey Price", "Linus Ullmark"]'::jsonb, 0,
 'Marc-Andre Fleury won the Vezina Trophy in 2021 with Vegas Golden Knights.', 'curated'),

('2027-03-05', 'hard', 'nhl',
 'Who won the Vezina Trophy as the NHL''s best goaltender in 2022, playing for New York Rangers?',
 '["Andrei Vasilevskiy", "Igor Shesterkin", "Marc-Andre Fleury", "Connor Hellebuyck"]'::jsonb, 1,
 'Igor Shesterkin won the Vezina Trophy in 2022 with New York Rangers.', 'curated'),

('2027-03-06', 'hard', 'nhl',
 'Who won the Vezina Trophy as the NHL''s best goaltender in 2023, playing for Boston Bruins?',
 '["Carey Price", "Linus Ullmark", "Marc-Andre Fleury", "Connor Hellebuyck"]'::jsonb, 1,
 'Linus Ullmark won the Vezina Trophy in 2023 with Boston Bruins.', 'curated'),

('2027-03-07', 'hard', 'nhl',
 'Who won the Vezina Trophy as the NHL''s best goaltender in 2025, playing for Winnipeg Jets?',
 '["Carey Price", "Connor Hellebuyck", "Linus Ullmark", "Pekka Rinne"]'::jsonb, 1,
 'Connor Hellebuyck won the Vezina Trophy in 2025 with Winnipeg Jets.', 'curated'),

('2027-03-08', 'hard', 'nhl',
 'Who won the Calder Memorial Trophy as NHL rookie of the year in 1981, playing for Quebec Nordiques?',
 '["Gary Suter", "Peter Stastny", "Sergei Makarov", "Dale Hawerchuk"]'::jsonb, 1,
 'Peter Stastny won the Calder Trophy in 1981 with Quebec Nordiques.', 'curated'),

('2027-03-09', 'hard', 'nhl',
 'Who won the Calder Memorial Trophy as NHL rookie of the year in 1982, playing for Winnipeg Jets?',
 '["Mario Lemieux", "Sergei Makarov", "Brian Leetch", "Dale Hawerchuk"]'::jsonb, 3,
 'Dale Hawerchuk won the Calder Trophy in 1982 with Winnipeg Jets.', 'curated'),

('2027-03-10', 'hard', 'nhl',
 'Who won the Calder Memorial Trophy as NHL rookie of the year in 1983, playing for Chicago Black Hawks?',
 '["Dale Hawerchuk", "Peter Stastny", "Steve Larmer", "Gary Suter"]'::jsonb, 2,
 'Steve Larmer won the Calder Trophy in 1983 with Chicago Black Hawks.', 'curated'),

('2027-03-11', 'hard', 'nhl',
 'Who won the Calder Memorial Trophy as NHL rookie of the year in 1985, playing for Pittsburgh Penguins?',
 '["Mario Lemieux", "Pavel Bure", "Gary Suter", "Brian Leetch"]'::jsonb, 0,
 'Mario Lemieux won the Calder Trophy in 1985 with Pittsburgh Penguins.', 'curated'),

('2027-03-12', 'hard', 'nhl',
 'Who won the Calder Memorial Trophy as NHL rookie of the year in 1989, playing for New York Rangers?',
 '["Teemu Selanne", "Mario Lemieux", "Steve Larmer", "Brian Leetch"]'::jsonb, 3,
 'Brian Leetch won the Calder Trophy in 1989 with New York Rangers.', 'curated'),

('2027-03-13', 'hard', 'nhl',
 'Who won the Calder Memorial Trophy as NHL rookie of the year in 1990, playing for Calgary Flames?',
 '["Daniel Alfredsson", "Gary Suter", "Pavel Bure", "Sergei Makarov"]'::jsonb, 3,
 'Sergei Makarov won the Calder Trophy in 1990 with Calgary Flames.', 'curated'),

('2027-03-14', 'hard', 'nhl',
 'Who won the Calder Memorial Trophy as NHL rookie of the year in 1992, playing for Vancouver Canucks?',
 '["Peter Forsberg", "Daniel Alfredsson", "Pavel Bure", "Teemu Selanne"]'::jsonb, 2,
 'Pavel Bure won the Calder Trophy in 1992 with Vancouver Canucks.', 'curated'),

('2027-03-15', 'hard', 'nhl',
 'Who won the Calder Memorial Trophy as NHL rookie of the year in 1993, playing for Winnipeg Jets?',
 '["Sergei Makarov", "Luc Robitaille", "Gary Suter", "Teemu Selanne"]'::jsonb, 3,
 'Teemu Selanne won the Calder Trophy in 1993 with Winnipeg Jets.', 'curated'),

('2027-03-16', 'hard', 'nhl',
 'Who won the Calder Memorial Trophy as NHL rookie of the year in 1995, playing for Quebec Nordiques?',
 '["Brian Leetch", "Sergei Makarov", "Peter Forsberg", "Scott Gomez"]'::jsonb, 2,
 'Peter Forsberg won the Calder Trophy in 1995 with Quebec Nordiques.', 'curated'),

('2027-03-17', 'hard', 'nhl',
 'Who won the Calder Memorial Trophy as NHL rookie of the year in 1996, playing for Ottawa Senators?',
 '["Peter Forsberg", "Daniel Alfredsson", "Chris Drury", "Teemu Selanne"]'::jsonb, 1,
 'Daniel Alfredsson won the Calder Trophy in 1996 with Ottawa Senators.', 'curated'),

('2027-03-18', 'hard', 'nhl',
 'Who won the Calder Memorial Trophy as NHL rookie of the year in 1999, playing for Colorado Avalanche?',
 '["Daniel Alfredsson", "Martin Brodeur", "Pavel Bure", "Chris Drury"]'::jsonb, 3,
 'Chris Drury won the Calder Trophy in 1999 with Colorado Avalanche.', 'curated'),

('2027-03-19', 'hard', 'nhl',
 'Who won the Calder Memorial Trophy as NHL rookie of the year in 2002, playing for Atlanta Thrashers?',
 '["Scott Gomez", "Peter Forsberg", "Daniel Alfredsson", "Dany Heatley"]'::jsonb, 3,
 'Dany Heatley won the Calder Trophy in 2002 with Atlanta Thrashers.', 'curated'),

('2027-03-20', 'hard', 'nhl',
 'Who won the Calder Memorial Trophy as NHL rookie of the year in 2006, playing for Washington Capitals?',
 '["Alexander Ovechkin", "Evgeni Malkin", "Chris Drury", "Scott Gomez"]'::jsonb, 0,
 'Alexander Ovechkin won the Calder Trophy in 2006 with Washington Capitals.', 'curated'),

('2027-03-21', 'hard', 'nhl',
 'Who won the Calder Memorial Trophy as NHL rookie of the year in 2007, playing for Pittsburgh Penguins?',
 '["Evgeni Malkin", "Jonathan Huberdeau", "Scott Gomez", "Gabriel Landeskog"]'::jsonb, 0,
 'Evgeni Malkin won the Calder Trophy in 2007 with Pittsburgh Penguins.', 'curated'),

('2027-03-22', 'hard', 'nhl',
 'Who won the Calder Memorial Trophy as NHL rookie of the year in 2008, playing for Chicago Blackhawks?',
 '["Evgeni Malkin", "Jonathan Huberdeau", "Gabriel Landeskog", "Patrick Kane"]'::jsonb, 3,
 'Patrick Kane won the Calder Trophy in 2008 with Chicago Blackhawks.', 'curated'),

('2027-03-23', 'hard', 'nhl',
 'Who won the Calder Memorial Trophy as NHL rookie of the year in 2011, playing for Carolina Hurricanes?',
 '["Jeff Skinner", "Alexander Ovechkin", "Jonathan Huberdeau", "Gabriel Landeskog"]'::jsonb, 0,
 'Jeff Skinner won the Calder Trophy in 2011 with Carolina Hurricanes.', 'curated'),

('2027-03-24', 'hard', 'nhl',
 'Who won the Calder Memorial Trophy as NHL rookie of the year in 2013, playing for Florida Panthers?',
 '["Patrick Kane", "Jonathan Huberdeau", "Jeff Skinner", "Alexander Ovechkin"]'::jsonb, 1,
 'Jonathan Huberdeau won the Calder Trophy in 2013 with Florida Panthers.', 'curated'),

('2027-03-25', 'hard', 'nhl',
 'Who won the Calder Memorial Trophy as NHL rookie of the year in 2014, playing for Colorado Avalanche?',
 '["Nathan MacKinnon", "Elias Pettersson", "Evgeni Malkin", "Gabriel Landeskog"]'::jsonb, 0,
 'Nathan MacKinnon won the Calder Trophy in 2014 with Colorado Avalanche.', 'curated'),

('2027-03-26', 'hard', 'nhl',
 'Who won the Calder Memorial Trophy as NHL rookie of the year in 2016, playing for Chicago Blackhawks?',
 '["Gabriel Landeskog", "Artemi Panarin", "Jeff Skinner", "Nathan MacKinnon"]'::jsonb, 1,
 'Artemi Panarin won the Calder Trophy in 2016 with Chicago Blackhawks.', 'curated'),

('2027-03-27', 'hard', 'nhl',
 'Who won the Calder Memorial Trophy as NHL rookie of the year in 2017, playing for Toronto Maple Leafs?',
 '["Gabriel Landeskog", "Artemi Panarin", "Cale Makar", "Auston Matthews"]'::jsonb, 3,
 'Auston Matthews won the Calder Trophy in 2017 with Toronto Maple Leafs.', 'curated'),

('2027-03-28', 'hard', 'nhl',
 'Who won the Calder Memorial Trophy as NHL rookie of the year in 2024, playing for Chicago Blackhawks?',
 '["Elias Pettersson", "Connor Bedard", "Jonathan Huberdeau", "Kirill Kaprizov"]'::jsonb, 1,
 'Connor Bedard won the Calder Trophy in 2024 with Chicago Blackhawks.', 'curated'),

('2027-03-29', 'hard', 'nhl',
 'Who won the Calder Memorial Trophy as NHL rookie of the year in 2025, playing for Montreal Canadiens?',
 '["Lane Hutson", "Jonathan Huberdeau", "Nathan MacKinnon", "Elias Pettersson"]'::jsonb, 0,
 'Lane Hutson won the Calder Trophy in 2025 with Montreal Canadiens.', 'curated'),

-- ============================ PWHL (53) ============================
('2026-09-07', 'hard', 'pwhl',
 'Which country won the IIHF Women''s World Championship in 1990?',
 '["Canada", "Sweden", "Russia", "Switzerland"]'::jsonb, 0,
 'Canada won gold at the 1990 IIHF Women''s World Championship, defeating United States in the final.', 'curated'),

('2026-09-08', 'hard', 'pwhl',
 'Which country won the IIHF Women''s World Championship in 1992?',
 '["Russia", "Sweden", "Finland", "Canada"]'::jsonb, 3,
 'Canada won gold at the 1992 IIHF Women''s World Championship, defeating United States in the final.', 'curated'),

('2026-09-09', 'hard', 'pwhl',
 'Which country won the IIHF Women''s World Championship in 1994?',
 '["Finland", "Canada", "Sweden", "Switzerland"]'::jsonb, 1,
 'Canada won gold at the 1994 IIHF Women''s World Championship, defeating United States in the final.', 'curated'),

('2026-09-10', 'hard', 'pwhl',
 'Which country won the IIHF Women''s World Championship in 1997?',
 '["Russia", "Canada", "Czechia", "Sweden"]'::jsonb, 1,
 'Canada won gold at the 1997 IIHF Women''s World Championship, defeating United States in the final.', 'curated'),

('2026-09-11', 'hard', 'pwhl',
 'Which country won the IIHF Women''s World Championship in 2001?',
 '["United States", "Finland", "Canada", "Switzerland"]'::jsonb, 2,
 'Canada won gold at the 2001 IIHF Women''s World Championship, defeating United States in the final.', 'curated'),

('2026-09-12', 'hard', 'pwhl',
 'Which country won the IIHF Women''s World Championship in 2004?',
 '["Finland", "Canada", "Sweden", "Switzerland"]'::jsonb, 1,
 'Canada won gold at the 2004 IIHF Women''s World Championship, defeating United States in the final.', 'curated'),

('2026-09-13', 'hard', 'pwhl',
 'Which country won the IIHF Women''s World Championship in 2005?',
 '["Russia", "Sweden", "United States", "Canada"]'::jsonb, 2,
 'United States won gold at the 2005 IIHF Women''s World Championship, defeating Canada in the final.', 'curated'),

('2026-09-14', 'hard', 'pwhl',
 'Which country won the IIHF Women''s World Championship in 2007?',
 '["Finland", "Canada", "United States", "Czechia"]'::jsonb, 1,
 'Canada won gold at the 2007 IIHF Women''s World Championship, defeating United States in the final.', 'curated'),

('2026-09-15', 'hard', 'pwhl',
 'Which country won the IIHF Women''s World Championship in 2008?',
 '["Switzerland", "United States", "Finland", "Russia"]'::jsonb, 1,
 'United States won gold at the 2008 IIHF Women''s World Championship, defeating Canada in the final.', 'curated'),

('2026-09-16', 'hard', 'pwhl',
 'Which country won the IIHF Women''s World Championship in 2009?',
 '["Russia", "Sweden", "Canada", "United States"]'::jsonb, 3,
 'United States won gold at the 2009 IIHF Women''s World Championship, defeating Canada in the final.', 'curated'),

('2026-09-17', 'hard', 'pwhl',
 'Which country won the IIHF Women''s World Championship in 2013?',
 '["Finland", "United States", "Switzerland", "Russia"]'::jsonb, 1,
 'United States won gold at the 2013 IIHF Women''s World Championship, defeating Canada in the final.', 'curated'),

('2026-09-18', 'hard', 'pwhl',
 'Which country won the IIHF Women''s World Championship in 2016?',
 '["Switzerland", "Canada", "United States", "Russia"]'::jsonb, 2,
 'United States won gold at the 2016 IIHF Women''s World Championship, defeating Canada in the final.', 'curated'),

('2026-09-19', 'hard', 'pwhl',
 'Which country won the IIHF Women''s World Championship in 2017?',
 '["United States", "Switzerland", "Finland", "Sweden"]'::jsonb, 0,
 'United States won gold at the 2017 IIHF Women''s World Championship, defeating Canada in the final.', 'curated'),

('2026-09-20', 'hard', 'pwhl',
 'Which country won the IIHF Women''s World Championship in 2019?',
 '["United States", "Sweden", "Finland", "Czechia"]'::jsonb, 0,
 'United States won gold at the 2019 IIHF Women''s World Championship, defeating Finland in the final.', 'curated'),

('2026-09-21', 'hard', 'pwhl',
 'Which country won the IIHF Women''s World Championship in 2021?',
 '["United States", "Russia", "Canada", "Finland"]'::jsonb, 2,
 'Canada won gold at the 2021 IIHF Women''s World Championship, defeating United States in the final.', 'curated'),

('2026-09-22', 'hard', 'pwhl',
 'Which country won the IIHF Women''s World Championship in 2023?',
 '["United States", "Czechia", "Sweden", "Russia"]'::jsonb, 0,
 'United States won gold at the 2023 IIHF Women''s World Championship, defeating Canada in the final.', 'curated'),

('2026-09-23', 'hard', 'pwhl',
 'Which country won the IIHF Women''s World Championship in 2024?',
 '["Canada", "Russia", "Czechia", "Switzerland"]'::jsonb, 0,
 'Canada won gold at the 2024 IIHF Women''s World Championship, defeating United States in the final.', 'curated'),

('2026-09-24', 'hard', 'pwhl',
 'Which country won the IIHF Women''s World Championship in 2025?',
 '["Canada", "United States", "Sweden", "Switzerland"]'::jsonb, 1,
 'United States won gold at the 2025 IIHF Women''s World Championship, defeating Canada in the final.', 'curated'),

('2026-09-25', 'hard', 'pwhl',
 'Which country won the 2022 Beijing Olympic women''s ice hockey gold medal, defeating the United States 3-2?',
 '["Sweden", "Canada", "Finland", "United States"]'::jsonb, 1,
 'Canada beat the United States 3-2 in the final to win gold at the 2022 Beijing Olympics.', 'curated'),

('2026-09-26', 'hard', 'pwhl',
 'Which country won the 2026 Milano Cortina Olympic women''s ice hockey gold medal, defeating Canada 2-1 in overtime?',
 '["United States", "Finland", "Switzerland", "Canada"]'::jsonb, 0,
 'The United States beat Canada 2-1 in overtime to win gold at the 2026 Milano Cortina Olympics.', 'curated'),

('2026-09-27', 'hard', 'pwhl',
 'Who won the Patty Kazmaier Award as NCAA women''s hockey''s top player in 1998, playing for New Hampshire?',
 '["Julie Chu", "Angela Ruggiero", "Brandy Fisher", "Brooke Whitney"]'::jsonb, 2,
 'Brandy Fisher won the Patty Kazmaier Award in 1998, playing for New Hampshire.', 'curated'),

('2026-09-28', 'hard', 'pwhl',
 'Who won the Patty Kazmaier Award as NCAA women''s hockey''s top player in 1999, playing for Harvard?',
 '["Ali Brewer", "Meghan Duggan", "A.J. Mleczko", "Krissy Wendell"]'::jsonb, 2,
 'A.J. Mleczko won the Patty Kazmaier Award in 1999, playing for Harvard.', 'curated'),

('2026-09-29', 'hard', 'pwhl',
 'Who won the Patty Kazmaier Award as NCAA women''s hockey''s top player in 2001, playing for Harvard?',
 '["Brandy Fisher", "Jessie Vetter", "Ali Brewer", "Jennifer Botterill"]'::jsonb, 3,
 'Jennifer Botterill won the Patty Kazmaier Award in 2001, playing for Harvard.', 'curated'),

('2026-09-30', 'hard', 'pwhl',
 'Who won the Patty Kazmaier Award as NCAA women''s hockey''s top player in 2002, playing for Northeastern?',
 '["A.J. Mleczko", "Angela Ruggiero", "Jessie Vetter", "Brooke Whitney"]'::jsonb, 3,
 'Brooke Whitney won the Patty Kazmaier Award in 2002, playing for Northeastern.', 'curated'),

('2026-10-01', 'hard', 'pwhl',
 'Who won the Patty Kazmaier Award as NCAA women''s hockey''s top player in 2004, playing for Harvard?',
 '["Angela Ruggiero", "Jennifer Botterill", "Julie Chu", "Ali Brewer"]'::jsonb, 0,
 'Angela Ruggiero won the Patty Kazmaier Award in 2004, playing for Harvard.', 'curated'),

('2026-10-02', 'hard', 'pwhl',
 'Who won the Patty Kazmaier Award as NCAA women''s hockey''s top player in 2005, playing for Minnesota?',
 '["Brandy Fisher", "Krissy Wendell", "Jessie Vetter", "A.J. Mleczko"]'::jsonb, 1,
 'Krissy Wendell won the Patty Kazmaier Award in 2005, playing for Minnesota.', 'curated'),

('2026-10-03', 'hard', 'pwhl',
 'Who won the Patty Kazmaier Award as NCAA women''s hockey''s top player in 2009, playing for Wisconsin?',
 '["Alex Carpenter", "Jessie Vetter", "Brianna Decker", "Krissy Wendell"]'::jsonb, 1,
 'Jessie Vetter won the Patty Kazmaier Award in 2009, playing for Wisconsin.', 'curated'),

('2026-10-04', 'hard', 'pwhl',
 'Who won the Patty Kazmaier Award as NCAA women''s hockey''s top player in 2011, playing for Wisconsin?',
 '["Meghan Duggan", "Brianna Decker", "Angela Ruggiero", "Alex Carpenter"]'::jsonb, 0,
 'Meghan Duggan won the Patty Kazmaier Award in 2011, playing for Wisconsin.', 'curated'),

('2026-10-05', 'hard', 'pwhl',
 'Who won the Patty Kazmaier Award as NCAA women''s hockey''s top player in 2012, playing for Wisconsin?',
 '["Ann-Renee Desbiels", "Brianna Decker", "Daryl Watts", "Julie Chu"]'::jsonb, 1,
 'Brianna Decker won the Patty Kazmaier Award in 2012, playing for Wisconsin.', 'curated'),

('2026-10-06', 'hard', 'pwhl',
 'Who won the Patty Kazmaier Award as NCAA women''s hockey''s top player in 2013, playing for Minnesota?',
 '["Amanda Kessel", "Jessie Vetter", "Alex Carpenter", "Julie Chu"]'::jsonb, 0,
 'Amanda Kessel won the Patty Kazmaier Award in 2013, playing for Minnesota.', 'curated'),

('2026-10-07', 'hard', 'pwhl',
 'Who won the Patty Kazmaier Award as NCAA women''s hockey''s top player in 2015, playing for Boston College?',
 '["Alex Carpenter", "Kendall Coyne", "Brianna Decker", "Meghan Duggan"]'::jsonb, 0,
 'Alex Carpenter won the Patty Kazmaier Award in 2015, playing for Boston College.', 'curated'),

('2026-10-08', 'hard', 'pwhl',
 'Who won the Patty Kazmaier Award as NCAA women''s hockey''s top player in 2016, playing for Northeastern?',
 '["Meghan Duggan", "Brianna Decker", "Kendall Coyne", "Amanda Kessel"]'::jsonb, 2,
 'Kendall Coyne won the Patty Kazmaier Award in 2016, playing for Northeastern.', 'curated'),

('2026-10-09', 'hard', 'pwhl',
 'Who won the Patty Kazmaier Award as NCAA women''s hockey''s top player in 2017, playing for Wisconsin?',
 '["Aerin Frankel", "Taylor Heise", "Ann-Renee Desbiels", "Alex Carpenter"]'::jsonb, 2,
 'Ann-Renee Desbiels won the Patty Kazmaier Award in 2017, playing for Wisconsin.', 'curated'),

('2026-10-10', 'hard', 'pwhl',
 'Who won the Patty Kazmaier Award as NCAA women''s hockey''s top player in 2018, playing for Boston College?',
 '["Alex Carpenter", "Taylor Heise", "Loren Gabel", "Daryl Watts"]'::jsonb, 3,
 'Daryl Watts won the Patty Kazmaier Award in 2018, playing for Boston College.', 'curated'),

('2026-10-11', 'hard', 'pwhl',
 'Who won the Patty Kazmaier Award as NCAA women''s hockey''s top player in 2019, playing for Clarkson?',
 '["Alex Carpenter", "Aerin Frankel", "Loren Gabel", "Izzy Daniel"]'::jsonb, 2,
 'Loren Gabel won the Patty Kazmaier Award in 2019, playing for Clarkson.', 'curated'),

('2026-10-12', 'hard', 'pwhl',
 'Who won the Patty Kazmaier Award as NCAA women''s hockey''s top player in 2021, playing for Northeastern?',
 '["Kendall Coyne", "Izzy Daniel", "Ann-Renee Desbiels", "Aerin Frankel"]'::jsonb, 3,
 'Aerin Frankel won the Patty Kazmaier Award in 2021, playing for Northeastern.', 'curated'),

('2026-10-13', 'hard', 'pwhl',
 'Who won the Patty Kazmaier Award as NCAA women''s hockey''s top player in 2022, playing for Minnesota?',
 '["Izzy Daniel", "Taylor Heise", "Daryl Watts", "Caroline Harvey"]'::jsonb, 1,
 'Taylor Heise won the Patty Kazmaier Award in 2022, playing for Minnesota.', 'curated'),

('2026-10-14', 'hard', 'pwhl',
 'Who won the Patty Kazmaier Award as NCAA women''s hockey''s top player in 2023, playing for Ohio State?',
 '["Ann-Renee Desbiels", "Alex Carpenter", "Aerin Frankel", "Sophie Jaques"]'::jsonb, 3,
 'Sophie Jaques won the Patty Kazmaier Award in 2023, playing for Ohio State.', 'curated'),

('2026-10-15', 'hard', 'pwhl',
 'Who won the Patty Kazmaier Award as NCAA women''s hockey''s top player in 2024, playing for Cornell?',
 '["Izzy Daniel", "Ann-Renee Desbiels", "Daryl Watts", "Caroline Harvey"]'::jsonb, 0,
 'Izzy Daniel won the Patty Kazmaier Award in 2024, playing for Cornell.', 'curated'),

('2026-10-16', 'hard', 'pwhl',
 'Who won the Patty Kazmaier Award as NCAA women''s hockey''s top player in 2026, playing for Wisconsin?',
 '["Caroline Harvey", "Daryl Watts", "Taylor Heise", "Sophie Jaques"]'::jsonb, 0,
 'Caroline Harvey won the Patty Kazmaier Award in 2026, playing for Wisconsin.', 'curated'),

('2026-10-17', 'hard', 'pwhl',
 'Which team won the Clarkson Cup, the CWHL''s championship, in 2009?',
 '["Calgary Inferno", "Montreal Stars", "Les Canadiennes de Montreal", "Minnesota Whitecaps"]'::jsonb, 1,
 'Montreal Stars won the Clarkson Cup in 2009, during the CWHL era (the league folded in 2019).', 'curated'),

('2026-10-18', 'hard', 'pwhl',
 'Which team won the Clarkson Cup, the CWHL''s championship, in 2010?',
 '["Toronto Furies", "Les Canadiennes de Montreal", "Minnesota Whitecaps", "Boston Blades"]'::jsonb, 2,
 'Minnesota Whitecaps won the Clarkson Cup in 2010, during the CWHL era (the league folded in 2019).', 'curated'),

('2026-10-19', 'hard', 'pwhl',
 'Which team won the Clarkson Cup, the CWHL''s championship, in 2012?',
 '["Montreal Stars", "Boston Blades", "Calgary Inferno", "Minnesota Whitecaps"]'::jsonb, 0,
 'Montreal Stars won the Clarkson Cup in 2012, during the CWHL era (the league folded in 2019).', 'curated'),

('2026-10-20', 'hard', 'pwhl',
 'Which team won the Clarkson Cup, the CWHL''s championship, in 2013?',
 '["Les Canadiennes de Montreal", "Boston Blades", "Toronto Furies", "Calgary Inferno"]'::jsonb, 1,
 'Boston Blades won the Clarkson Cup in 2013, during the CWHL era (the league folded in 2019).', 'curated'),

('2026-10-21', 'hard', 'pwhl',
 'Which team won the Clarkson Cup, the CWHL''s championship, in 2014?',
 '["Toronto Furies", "Montreal Stars", "Calgary Inferno", "Boston Blades"]'::jsonb, 0,
 'Toronto Furies won the Clarkson Cup in 2014, during the CWHL era (the league folded in 2019).', 'curated'),

('2026-10-22', 'hard', 'pwhl',
 'Which team won the Clarkson Cup, the CWHL''s championship, in 2016?',
 '["Calgary Inferno", "Boston Blades", "Toronto Furies", "Minnesota Whitecaps"]'::jsonb, 0,
 'Calgary Inferno won the Clarkson Cup in 2016, during the CWHL era (the league folded in 2019).', 'curated'),

('2026-10-23', 'hard', 'pwhl',
 'Which team won the Clarkson Cup, the CWHL''s championship, in 2017?',
 '["Toronto Furies", "Calgary Inferno", "Minnesota Whitecaps", "Les Canadiennes de Montreal"]'::jsonb, 3,
 'Les Canadiennes de Montreal won the Clarkson Cup in 2017, during the CWHL era (the league folded in 2019).', 'curated'),

('2026-10-24', 'hard', 'pwhl',
 'Which team won the Clarkson Cup, the CWHL''s championship, in 2019?',
 '["Les Canadiennes de Montreal", "Calgary Inferno", "Montreal Stars", "Boston Blades"]'::jsonb, 1,
 'Calgary Inferno won the Clarkson Cup in 2019, during the CWHL era (the league folded in 2019).', 'curated'),

('2026-10-25', 'hard', 'pwhl',
 'Which team won the Isobel Cup, the NWHL/PHF''s championship, in 2016?',
 '["Minnesota Whitecaps", "Boston Pride", "Buffalo Beauts", "Toronto Six"]'::jsonb, 1,
 'Boston Pride won the Isobel Cup in 2016, during the NWHL/PHF era (that league was bought out and folded into the PWHL in 2023).', 'curated'),

('2026-10-26', 'hard', 'pwhl',
 'Which team won the Isobel Cup, the NWHL/PHF''s championship, in 2017?',
 '["Minnesota Whitecaps", "Buffalo Beauts", "Metropolitan Riveters", "Toronto Six"]'::jsonb, 1,
 'Buffalo Beauts won the Isobel Cup in 2017, during the NWHL/PHF era (that league was bought out and folded into the PWHL in 2023).', 'curated'),

('2026-10-27', 'hard', 'pwhl',
 'Which team won the Isobel Cup, the NWHL/PHF''s championship, in 2018?',
 '["Metropolitan Riveters", "Buffalo Beauts", "Toronto Six", "Boston Pride"]'::jsonb, 0,
 'Metropolitan Riveters won the Isobel Cup in 2018, during the NWHL/PHF era (that league was bought out and folded into the PWHL in 2023).', 'curated'),

('2026-10-28', 'hard', 'pwhl',
 'Which team won the Isobel Cup, the NWHL/PHF''s championship, in 2019?',
 '["Boston Pride", "Metropolitan Riveters", "Toronto Six", "Minnesota Whitecaps"]'::jsonb, 3,
 'Minnesota Whitecaps won the Isobel Cup in 2019, during the NWHL/PHF era (that league was bought out and folded into the PWHL in 2023).', 'curated'),

('2026-10-29', 'hard', 'pwhl',
 'Which team won the Isobel Cup, the NWHL/PHF''s championship, in 2023?',
 '["Buffalo Beauts", "Minnesota Whitecaps", "Toronto Six", "Metropolitan Riveters"]'::jsonb, 2,
 'Toronto Six won the Isobel Cup in 2023, during the NWHL/PHF era (that league was bought out and folded into the PWHL in 2023).', 'curated')

on conflict (question_date, tier, sport, team) do nothing;
